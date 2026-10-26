"""Browser federation flow for Identity Center."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from openjiuwen_runtime.service import (
    FederationBindingError,
    FederationConnection,
    FederationCoordinator,
    FederationError,
    LocalPrincipal,
    SystemContext,
    UnknownFederationConnection,
)
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from identity_center.core.auth.service import IdentityAuthService
from identity_center.infrastructure.config import Settings
from identity_center.infrastructure.utils import utc_now
from identity_center.models.identity_models import (
    FEDERATION_CONNECTION_TABLE_DEF,
    FEDERATION_LOGIN_CODE_TABLE_DEF,
    FEDERATION_LOGIN_STATE_TABLE_DEF,
    FEDERATION_ROLE_MAPPING_TABLE_DEF,
)
from identity_center.security.federation_code import open_token, seal_token

from .connections import (
    ConfiguredFederation,
    FederationRoleMapping,
    _configured_federations,
)
from .contracts import FederationTokenResult
from .oidc_provider import ValidatedOidcCallback
from .store import IdentityCenterFederatedIdentityStore


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
            if registration.provider is not None
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
    ) -> IdentityFederationService:
        registrations = _configured_federations(settings, handler)
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

    def _registration(self, connection_id: str) -> ConfiguredFederation:
        registration = self._connections.get(connection_id)
        if registration is None:
            raise UnknownFederationConnection("unknown federation connection")
        return registration

    def flow(self, connection_id: str) -> str:
        return self._registration(connection_id).flow

    async def browser_configuration(self, connection_id: str) -> dict[str, Any]:
        registration = self._registration(connection_id)
        options = registration.options
        if options is None or options.flow != "browser_pkce":
            raise FederationError("connection does not use browser PKCE")
        # Validate discovery server-side before exposing public client parameters.
        await registration.verifier.discovery()
        return {
            "connection_id": connection_id,
            "flow": options.flow,
            "authority": options.issuer,
            "client_id": options.client_id,
            "scope": options.scope,
            "automatic_silent_renew": options.automatic_silent_renew,
        }

    async def login_with_access_token(
        self, connection_id: str, token: str
    ) -> dict[str, Any] | str:
        registration = self._registration(connection_id)
        options = registration.options
        if options is None or options.flow != "browser_pkce":
            raise FederationError("connection does not accept browser tokens")
        verifier = registration.verifier
        claims = await verifier.validate_access_token(
            {"access_token": token, "token_type": "Bearer"},
            audience=options.access_token_audience,
        )
        identity = verifier.identity_from_claims(registration.connection, claims)
        # No authorization-code callback is fabricated: the verified token is
        # resolved through the same transactional identity store directly.
        principal = await self._store.resolve_or_create(
            registration.connection, identity
        )
        result = await IdentityAuthService(self._handler).issue_for_user_id(
            principal.user_id
        )
        if isinstance(result, dict) and options.handoff_access_token:
            result["federation"] = {
                "connection_id": connection_id,
                "user_id": principal.user_id,
                "access_token": token,
                "token_type": "Bearer",
            }
        return result

    async def refresh_token_handoff(
        self, connection_id: str, token: str, user_id: str
    ) -> dict[str, str]:
        registration = self._registration(connection_id)
        options = registration.options
        if (
            options is None
            or options.flow != "browser_pkce"
            or not options.handoff_access_token
        ):
            raise FederationError("connection does not hand off browser tokens")
        claims = await registration.verifier.validate_access_token(
            {"access_token": token, "token_type": "Bearer"},
            audience=options.access_token_audience,
        )
        principal = await self._store.find(
            connection_id=connection_id,
            issuer=registration.connection.issuer,
            external_subject=claims["sub"],
        )
        user = await IdentityAuthService(self._handler).get_user(user_id)
        identity_mismatch = principal is None or principal.user_id != user_id
        if identity_mismatch or user is None or user.status != "active":
            raise FederationError(
                "customer token does not match the active platform user"
            )
        return {
            "connection_id": connection_id,
            "user_id": user_id,
            "access_token": token,
            "token_type": "Bearer",
        }

    def requires_browser_binding(self, connection_id: str) -> bool:
        self._require_coordinator(connection_id)
        return self._connections[connection_id.strip()].provider_type == "oidc"

    @property
    def secure_browser_cookie(self) -> bool:
        return any(
            item.options is not None
            and urlsplit(item.options.redirect_uri).scheme == "https"
            for item in self._connections.values()
        )

    @property
    def browser_cookie_name(self) -> str:
        prefix = "__Host-" if self.secure_browser_cookie else ""
        return prefix + "identity_federation_browser"

    @property
    def browser_cookie_path(self) -> str:
        return (
            "/"
            if self.secure_browser_cookie
            else self.public_path("/v1/auth/federation")
        )

    @property
    def browser_cookie_ttl(self) -> int:
        return (
            self._settings.federation_request_ttl_seconds
            + self._settings.federation_code_ttl_seconds
        )

    async def begin_login(
        self, connection_id: str, return_to: str, *, browser_binding: str | None = None
    ) -> str:
        coordinator = self._require_coordinator(connection_id)
        if self.requires_browser_binding(connection_id) and not browser_binding:
            raise FederationError("OIDC login must be bound to a browser")
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
                    data={"browser_hash": _hash_code(browser_binding)}
                    if browser_binding
                    else None,
                    expires_at=now
                    + timedelta(seconds=self._settings.federation_request_ttl_seconds),
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
        *,
        browser_binding: str | None = None,
    ) -> tuple[str, LocalPrincipal]:
        coordinator = self._require_coordinator(connection_id)
        code_data = None
        if self.requires_browser_binding(connection_id):
            request_id = str(parameters.get("state") or "")
            row = await self._take_request(
                connection_id, request_id, browser_binding=browser_binding
            )
            data = row["data"] or {}
            code_data = {"browser_hash": data["browser_hash"]}
            # Consume before contacting the issuer: parallel callbacks cannot
            # redeem the same state or invalidate another callback in flight.
            trusted = ValidatedOidcCallback(parameters, data)
            authentication = await coordinator.consume_callback(connection_id, trusted)
            return_to = str(row["return_to"])
        else:
            authentication = await coordinator.consume_callback(
                connection_id, parameters
            )
            return_to = await self._consume_request(
                connection_id, authentication.authorization_request_id
            )
        principal = await coordinator.resolve_or_create(
            connection_id,
            authentication.identity,
        )
        if isinstance(authentication, FederationTokenResult) and (
            self._connections[connection_id].options is None
            or self._connections[connection_id].options.handoff_access_token
        ):
            code_data = {
                **(code_data or {}),
                "federation": {
                    "connection_id": connection_id,
                    "user_id": principal.user_id,
                    "access_token": seal_token(
                        authentication.token_bundle["access_token"], principal.user_id
                    ),
                    "token_type": "Bearer",
                },
            }
        code = await self._create_login_code(principal.user_id, data=code_data)
        return _append_query(return_to, {"federation_code": code}), principal

    async def exchange_code(
        self, code: str, *, browser_binding: str | None = None
    ) -> dict[str, Any] | str:
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
            data = row.get("data") or {}
            if data.get("browser_hash") and not _matches_browser(data, browser_binding):
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
        result = await IdentityAuthService(self._handler).issue_for_user_id(user_id)
        if isinstance(result, dict) and data.get("federation"):
            federation = dict(data["federation"])
            federation["access_token"] = open_token(federation["access_token"], user_id)
            result["federation"] = federation
        return result

    async def close(self) -> None:
        for registration in self._connections.values():
            if registration.verifier is not None:
                await registration.verifier.close()
        await self._store.close()

    def _require_coordinator(self, connection_id: str) -> FederationCoordinator:
        self._registration(connection_id)
        coordinator = self._coordinators.get(str(connection_id or "").strip())
        if coordinator is None:
            raise FederationError("connection does not use a server callback")
        return coordinator

    async def _consume_request(self, connection_id: str, request_id: str) -> str:
        return str((await self._take_request(connection_id, request_id))["return_to"])

    async def _take_request(
        self, connection_id: str, request_id: str, *, browser_binding: str | None = None
    ) -> dict[str, Any]:
        async with self._system_context.transaction() as session:
            row = await _one_mapping(
                session,
                select(self._state_table).where(
                    self._state_table.c.request_id == request_id
                ),
            )
            if row is None or str(row["connection_id"]) != connection_id:
                raise FederationError("federation login request is missing or expired")
            data = row.get("data") or {}
            if self.requires_browser_binding(connection_id) and not _matches_browser(
                data, browser_binding
            ):
                raise FederationError("OIDC login browser does not match")
            deleted = await session.execute(
                delete(self._state_table).where(
                    self._state_table.c.request_id == request_id
                )
            )
            if deleted.rowcount != 1:
                raise FederationError("federation login request is missing or expired")
            if _as_utc(row["expires_at"]) <= utc_now():
                raise FederationError("federation login request is missing or expired")
            return dict(row)

    async def _create_login_code(
        self, user_id: str, *, data: dict[str, Any] | None = None
    ) -> str:
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
                    data=data,
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


def _validate_return_to(value: str) -> str:
    normalized = str(value or "/auth").strip() or "/auth"
    parsed = urlsplit(normalized)
    has_external_origin = bool(parsed.scheme or parsed.netloc)
    reserved = {"federation_code", "federation_error"}
    invalid_target = len(normalized) > 512 or has_external_origin or parsed.fragment
    if (
        invalid_target
        or parsed.path != "/auth"
        or any(key in reserved for key, _ in parse_qsl(parsed.query))
    ):
        raise FederationError("federation return_to must be the local /auth route")
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def _hash_code(code: str) -> str:
    normalized = str(code or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _matches_browser(data: dict[str, Any], binding: str | None) -> bool:
    expected = data.get("browser_hash")
    return bool(
        binding
        and isinstance(expected, str)
        and expected
        and secrets.compare_digest(expected, _hash_code(binding))
    )


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
