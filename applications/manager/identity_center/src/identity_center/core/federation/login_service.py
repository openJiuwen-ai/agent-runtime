"""Browser federation flow for Identity Center."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from openjiuwen_runtime.service import (
    FederationBindingError,
    FederationConnection,
    FederationCoordinator,
    FederationError,
    FederationProvider,
    LocalPrincipal,
    SystemContext,
    UnknownFederationConnection,
)

from identity_center.core.auth.service import IdentityAuthService
from identity_center.infrastructure.config import Settings
from identity_center.infrastructure.utils import utc_now
from identity_center.models.identity_models import (
    FEDERATION_CONNECTION_TABLE_DEF,
    FEDERATION_LOGIN_CODE_TABLE_DEF,
    FEDERATION_LOGIN_STATE_TABLE_DEF,
    FEDERATION_ROLE_MAPPING_TABLE_DEF,
)

from .provider import DemoFederationProvider
from .store import IdentityCenterFederatedIdentityStore


@dataclass(frozen=True)
class FederationRoleMapping:
    claim_name: str
    claim_value: str
    local_role: str


@dataclass(frozen=True)
class ConfiguredFederation:
    connection: FederationConnection
    provider_type: str
    provider: FederationProvider
    role_mappings: tuple[FederationRoleMapping, ...] = ()


class IdentityFederationService:
    """Join external authentication to local JWT/refresh-token issuance."""

    def __init__(
        self,
        *,
        handler: SQLAlchemyHandler,
        settings: Settings,
        registrations: tuple[ConfiguredFederation, ...],
    ) -> None:
        self._handler = handler
        self._settings = settings
        self._system_context = SystemContext(db=handler)
        self._store = IdentityCenterFederatedIdentityStore(handler)
        self._connections = {
            registration.connection.connection_id: registration
            for registration in registrations
        }
        self._coordinators = {
            registration.connection.connection_id: FederationCoordinator(
                provider=registration.provider,
                identity_store=self._store,
                connections={
                    registration.connection.connection_id: registration.connection
                },
            )
            for registration in registrations
        }
        self._connection_table = handler.get_table(
            FEDERATION_CONNECTION_TABLE_DEF.table_name
        )
        self._state_table = handler.get_table(
            FEDERATION_LOGIN_STATE_TABLE_DEF.table_name
        )
        self._code_table = handler.get_table(FEDERATION_LOGIN_CODE_TABLE_DEF.table_name)
        self._role_mapping_table = handler.get_table(
            FEDERATION_ROLE_MAPPING_TABLE_DEF.table_name
        )

    @classmethod
    async def create(
        cls,
        handler: SQLAlchemyHandler,
        settings: Settings,
    ) -> "IdentityFederationService":
        registrations = _configured_federations(settings)
        service = cls(
            handler=handler,
            settings=settings,
            registrations=registrations,
        )
        await service._persist_connections()
        return service

    @property
    def connections(self) -> tuple[FederationConnection, ...]:
        return tuple(item.connection for item in self._connections.values())

    @property
    def demo_enabled(self) -> bool:
        return bool(self._settings.federation_demo_enabled)

    @property
    def demo_admin_group(self) -> str:
        return self._settings.federation_demo_admin_group.strip()

    def public_path(self, path: str) -> str:
        normalized_path = "/" + str(path or "").lstrip("/")
        prefix = str(self._settings.federation_public_path_prefix or "").strip()
        prefix = prefix.rstrip("/")
        return f"{prefix}{normalized_path}" if prefix else normalized_path

    async def begin_login(self, connection_id: str, return_to: str) -> str:
        coordinator = self._require_coordinator(connection_id)
        safe_return_to = _validate_return_to(return_to)
        request_id = secrets.token_urlsafe(32)
        now = utc_now()
        async with self._system_context.transaction() as session:
            await session.execute(
                delete(self._state_table).where(self._state_table.c.expires_at <= now)
            )
            await session.execute(
                insert(self._state_table).values(
                    request_id=request_id,
                    connection_id=connection_id,
                    return_to=safe_return_to,
                    expires_at=now
                    + timedelta(
                        seconds=self._settings.federation_request_ttl_seconds
                    ),
                    created_at=now,
                    updated_at=now,
                )
            )
        try:
            return await coordinator.begin_login(connection_id, request_id)
        except BaseException:
            await self._handler.delete(
                FEDERATION_LOGIN_STATE_TABLE_DEF.table_name,
                {"request_id": request_id},
            )
            raise

    async def require_pending_request(
        self,
        connection_id: str,
        request_id: str,
    ) -> None:
        row = await self._handler.get(
            FEDERATION_LOGIN_STATE_TABLE_DEF.table_name,
            {"request_id": request_id},
        )
        if row is None or str(getattr(row, "connection_id", "")) != connection_id:
            raise FederationError("federation login request is missing or expired")
        if _as_utc(getattr(row, "expires_at", None)) <= utc_now():
            await self._handler.delete(
                FEDERATION_LOGIN_STATE_TABLE_DEF.table_name,
                {"request_id": request_id},
            )
            raise FederationError("federation login request is missing or expired")

    async def complete_callback(
        self,
        connection_id: str,
        parameters: dict[str, str],
    ) -> tuple[str, LocalPrincipal]:
        coordinator = self._require_coordinator(connection_id)
        authentication = await coordinator.consume_callback(connection_id, parameters)
        return_to = await self._consume_request(
            connection_id,
            authentication.authorization_request_id,
        )
        principal = await coordinator.resolve_or_create(
            connection_id,
            authentication.identity,
        )
        code = await self._create_login_code(principal.user_id)
        return _append_query(return_to, {"federation_code": code}), principal

    async def exchange_code(self, code: str) -> dict[str, Any] | str:
        code_hash = _hash_code(code)
        async with self._system_context.transaction() as session:
            row = await _one_mapping(
                session,
                select(self._code_table).where(
                    self._code_table.c.code_hash == code_hash
                ),
            )
            if row is None:
                return "invalid_federation_code"
            deleted = await session.execute(
                delete(self._code_table).where(
                    self._code_table.c.code_hash == code_hash
                )
            )
            if deleted.rowcount != 1:
                return "invalid_federation_code"
            if _as_utc(row["expires_at"]) <= utc_now():
                return "invalid_federation_code"
            user_id = str(row["user_id"])
        return await IdentityAuthService(self._handler).issue_for_user_id(user_id)

    async def close(self) -> None:
        await self._store.close()

    def _require_coordinator(self, connection_id: str) -> FederationCoordinator:
        coordinator = self._coordinators.get(str(connection_id or "").strip())
        if coordinator is None:
            raise UnknownFederationConnection(
                f"unknown federation connection: {connection_id or '<empty>'}"
            )
        return coordinator

    async def _consume_request(self, connection_id: str, request_id: str) -> str:
        async with self._system_context.transaction() as session:
            row = await _one_mapping(
                session,
                select(self._state_table).where(
                    self._state_table.c.request_id == request_id
                ),
            )
            if row is None or str(row["connection_id"]) != connection_id:
                raise FederationError("federation login request is missing or expired")
            deleted = await session.execute(
                delete(self._state_table).where(
                    self._state_table.c.request_id == request_id
                )
            )
            if deleted.rowcount != 1:
                raise FederationError("federation login request is missing or expired")
            if _as_utc(row["expires_at"]) <= utc_now():
                raise FederationError("federation login request is missing or expired")
            return str(row["return_to"])

    async def _create_login_code(self, user_id: str) -> str:
        code = secrets.token_urlsafe(32)
        now = utc_now()
        async with self._system_context.transaction() as session:
            await session.execute(
                delete(self._code_table).where(self._code_table.c.expires_at <= now)
            )
            await session.execute(
                insert(self._code_table).values(
                    code_hash=_hash_code(code),
                    user_id=user_id,
                    expires_at=now
                    + timedelta(seconds=self._settings.federation_code_ttl_seconds),
                    created_at=now,
                    updated_at=now,
                )
            )
        return code

    async def _persist_connections(self) -> None:
        for attempt in range(3):
            try:
                await self._persist_connections_once()
                return
            except IntegrityError:
                # Multiple Identity Center replicas may initialize the same trusted
                # connection at once. The unique keys elect a winner; the loser
                # retries and then reconciles the persisted configuration.
                if attempt == 2:
                    raise
                await asyncio.sleep(0.01 * (attempt + 1))

    async def _persist_connections_once(self) -> None:
        async with self._system_context.transaction() as session:
            for registration in self._connections.values():
                connection = registration.connection
                row = await _one_mapping(
                    session,
                    select(self._connection_table).where(
                        self._connection_table.c.connection_id
                        == connection.connection_id
                    ),
                )
                now = utc_now()
                if row is None:
                    await session.execute(
                        insert(self._connection_table).values(
                            connection_id=connection.connection_id,
                            provider_type=registration.provider_type,
                            issuer=connection.issuer,
                            group_id=connection.organization_id,
                            name=connection.organization_name,
                            default_role=connection.default_role,
                            status="active",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    persisted_binding = (
                        str(row["provider_type"]),
                        str(row["issuer"]),
                        str(row["group_id"]),
                    )
                    configured_binding = (
                        registration.provider_type,
                        connection.issuer,
                        connection.organization_id,
                    )
                    if persisted_binding != configured_binding:
                        raise FederationBindingError(
                            "connection_id is already bound to different federation settings"
                        )
                    await session.execute(
                        update(self._connection_table)
                        .where(
                            self._connection_table.c.connection_id
                            == connection.connection_id
                        )
                        .values(
                            name=connection.organization_name,
                            default_role=connection.default_role,
                            status="active",
                            updated_at=now,
                        )
                    )

                normalized_mappings = {
                    (
                        mapping.claim_name.strip(),
                        mapping.claim_value.strip(),
                        mapping.local_role.strip(),
                    )
                    for mapping in registration.role_mappings
                }
                if any(not all(mapping) for mapping in normalized_mappings):
                    raise FederationBindingError(
                        "federation role mappings must not contain empty values"
                    )
                existing_result = await session.execute(
                    select(self._role_mapping_table).where(
                        self._role_mapping_table.c.connection_id
                        == connection.connection_id
                    )
                )
                existing_mappings = {
                    (
                        str(row["claim_name"]),
                        str(row["claim_value"]),
                        str(row["local_role"]),
                    ): row
                    for row in existing_result.mappings().all()
                }
                for mapping, row in existing_mappings.items():
                    if mapping not in normalized_mappings:
                        await session.execute(
                            delete(self._role_mapping_table).where(
                                self._role_mapping_table.c.id == row["id"]
                            )
                        )
                for claim_name, claim_value, local_role in sorted(
                    normalized_mappings - set(existing_mappings)
                ):
                    await session.execute(
                        insert(self._role_mapping_table).values(
                            connection_id=connection.connection_id,
                            claim_name=claim_name,
                            claim_value=claim_value,
                            local_role=local_role,
                            created_at=now,
                            updated_at=now,
                        )
                    )


def _configured_federations(settings: Settings) -> tuple[ConfiguredFederation, ...]:
    if not settings.federation_demo_enabled:
        return ()
    connection = FederationConnection(
        connection_id="enterprise-demo",
        issuer="https://idp.enterprise-demo.example",
        organization_id="federated-enterprise-demo",
        organization_name="Enterprise Demo SSO",
        default_role="member",
    )
    admin_group = settings.federation_demo_admin_group.strip()
    role_mappings = (
        (
            FederationRoleMapping(
                claim_name="groups",
                claim_value=admin_group,
                local_role="admin",
            ),
        )
        if admin_group
        else ()
    )
    return (
        ConfiguredFederation(
            connection=connection,
            provider_type="demo",
            provider=DemoFederationProvider(settings.federation_public_path_prefix),
            role_mappings=role_mappings,
        ),
    )


def _validate_return_to(value: str) -> str:
    normalized = str(value or "/auth").strip() or "/auth"
    parsed = urlsplit(normalized)
    has_external_origin = bool(parsed.scheme or parsed.netloc)
    if has_external_origin or parsed.fragment or parsed.path != "/auth":
        raise FederationError("federation return_to must be the local /auth route")
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def _hash_code(code: str) -> str:
    normalized = str(code or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _as_utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise FederationError("federation record has invalid expiration time")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _append_query(url: str, values: dict[str, str]) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(values.items())
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


async def _one_mapping(session: Any, statement: Any) -> Any | None:
    result = await session.execute(statement)
    return result.mappings().one_or_none()


__all__ = [
    "ConfiguredFederation",
    "FederationRoleMapping",
    "IdentityFederationService",
]
