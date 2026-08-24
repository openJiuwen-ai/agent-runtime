"""Transactional federation identity store backed by the Identity Center DB."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from openjiuwen_runtime.service import (
    ExternalIdentity,
    FederatedIdentityStore,
    FederationBindingError,
    FederationConnection,
    LocalPrincipal,
    SystemContext,
)

from identity_center.infrastructure.utils import utc_now
from identity_center.models.identity_models import (
    APP_USER_TABLE_DEF,
    FEDERATED_IDENTITY_TABLE_DEF,
    FEDERATION_CONNECTION_TABLE_DEF,
    FEDERATION_ROLE_MAPPING_TABLE_DEF,
    ORG_TABLE_DEF,
    USER_ORG_MEMBERSHIP_TABLE_DEF,
)


class IdentityCenterFederatedIdentityStore(FederatedIdentityStore):
    """Map an external subject to local user/org rows in one DB transaction."""

    def __init__(self, handler: SQLAlchemyHandler) -> None:
        if not callable(getattr(handler, "session_factory", None)):
            raise TypeError("federated identity store requires a SQLAlchemy DB handler")
        self._handler = handler
        self._system_context = SystemContext(db=handler)
        self._users = handler.get_table(APP_USER_TABLE_DEF.table_name)
        self._orgs = handler.get_table(ORG_TABLE_DEF.table_name)
        self._memberships = handler.get_table(USER_ORG_MEMBERSHIP_TABLE_DEF.table_name)
        self._connections = handler.get_table(
            FEDERATION_CONNECTION_TABLE_DEF.table_name
        )
        self._identities = handler.get_table(FEDERATED_IDENTITY_TABLE_DEF.table_name)
        self._role_mappings = handler.get_table(
            FEDERATION_ROLE_MAPPING_TABLE_DEF.table_name
        )

    async def resolve_or_create(
        self,
        connection: FederationConnection,
        identity: ExternalIdentity,
    ) -> LocalPrincipal:
        self.validate_binding(connection, identity)
        for attempt in range(3):
            try:
                return await self._resolve_or_create_once(connection, identity)
            except IntegrityError:
                # Concurrent first login: one transaction wins the unique external
                # identity key. Retry the complete operation so the current verified
                # claims still refresh the user's profile and effective role.
                if attempt == 2:
                    raise
                await asyncio.sleep(0.01 * (attempt + 1))
        raise AssertionError("unreachable")

    async def find(
        self,
        *,
        connection_id: str,
        issuer: str,
        external_subject: str,
    ) -> LocalPrincipal | None:
        identity_key = _external_identity_key(
            connection_id,
            issuer,
            external_subject,
        )
        async with self._system_context.transaction() as session:
            identity_row = await _one_mapping(
                session,
                select(self._identities).where(
                    self._identities.c.identity_key == identity_key,
                ),
            )
            if identity_row is None:
                return None
            _validate_identity_row(
                identity_row,
                connection_id=connection_id,
                issuer=issuer,
                external_subject=external_subject,
            )
            connection_row = await _one_mapping(
                session,
                select(self._connections).where(
                    self._connections.c.connection_id == connection_id
                ),
            )
            user_row = await _one_mapping(
                session,
                select(self._users).where(
                    self._users.c.user_id == identity_row["user_id"]
                ),
            )
            if connection_row is None or user_row is None:
                raise FederationBindingError(
                    "federated identity references missing connection or user"
                )
            roles = await self._resolve_roles(
                session,
                connection_id,
                connection_row,
                identity_row.get("attributes"),
            )
            return _principal(
                connection_row,
                user_row,
                identity_row.get("attributes"),
                roles,
            )

    async def close(self) -> None:
        """The Identity Center owns the shared database lifecycle."""

    async def _resolve_or_create_once(
        self,
        connection: FederationConnection,
        identity: ExternalIdentity,
    ) -> LocalPrincipal:
        async with self._system_context.transaction() as session:
            connection_row = await _one_mapping(
                session,
                select(self._connections).where(
                    self._connections.c.connection_id == connection.connection_id
                ),
            )
            _validate_connection_row(connection, connection_row)
            await self._ensure_active_org(session, connection)
            roles = await self._resolve_roles(
                session,
                connection.connection_id,
                connection_row,
                identity.attributes,
            )
            is_admin = "admin" in roles

            identity_key = _external_identity_key(
                identity.connection_id,
                identity.issuer,
                identity.external_subject,
            )
            identity_row = await _one_mapping(
                session,
                select(self._identities).where(
                    self._identities.c.identity_key == identity_key,
                ),
            )
            if identity_row is not None:
                _validate_identity_row(
                    identity_row,
                    connection_id=identity.connection_id,
                    issuer=identity.issuer,
                    external_subject=identity.external_subject,
                )
            now = utc_now()
            if identity_row is None:
                user_id = f"fuser_{uuid4().hex}"
                await session.execute(
                    insert(self._users).values(
                        user_id=user_id,
                        display_name=identity.display_name,
                        is_admin=is_admin,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.execute(
                    insert(self._identities).values(
                        connection_id=identity.connection_id,
                        issuer=identity.issuer,
                        external_subject=identity.external_subject,
                        identity_key=identity_key,
                        user_id=user_id,
                        attributes=identity.attributes,
                        first_login_at=now,
                        last_login_at=now,
                    )
                )
                await session.execute(
                    insert(self._memberships).values(
                        user_id=user_id,
                        group_id=connection.organization_id,
                        created_at=now,
                    )
                )
            else:
                user_id = str(identity_row["user_id"])
                user_row = await _one_mapping(
                    session,
                    select(self._users).where(self._users.c.user_id == user_id),
                )
                if user_row is None:
                    raise FederationBindingError(
                        "federated identity references a missing local user"
                    )
                await session.execute(
                    update(self._users)
                    .where(self._users.c.user_id == user_id)
                    .values(
                        display_name=identity.display_name,
                        is_admin=is_admin,
                        updated_at=now,
                    )
                )
                await session.execute(
                    update(self._identities)
                    .where(self._identities.c.id == identity_row["id"])
                    .values(attributes=identity.attributes, last_login_at=now)
                )
                membership = await _one_mapping(
                    session,
                    select(self._memberships).where(
                        self._memberships.c.user_id == user_id,
                        self._memberships.c.group_id == connection.organization_id,
                    ),
                )
                if membership is None:
                    await session.execute(
                        insert(self._memberships).values(
                            user_id=user_id,
                            group_id=connection.organization_id,
                            created_at=now,
                        )
                    )

            user_row = await _one_mapping(
                session,
                select(self._users).where(self._users.c.user_id == user_id),
            )
            if user_row is None:
                raise FederationBindingError("local user was not persisted")
            return _principal(connection_row, user_row, identity.attributes, roles)

    async def _resolve_roles(
        self,
        session: Any,
        connection_id: str,
        connection_row: Any,
        attributes: dict[str, Any] | None,
    ) -> tuple[str, ...]:
        roles = {str(connection_row["default_role"])}
        claims = attributes if isinstance(attributes, dict) else {}
        result = await session.execute(
            select(self._role_mappings).where(
                self._role_mappings.c.connection_id == connection_id
            )
        )
        for mapping in result.mappings().all():
            claim_values = _claim_values(claims.get(str(mapping["claim_name"])))
            if str(mapping["claim_value"]) in claim_values:
                roles.add(str(mapping["local_role"]))
        return tuple(sorted(role for role in roles if role))

    async def _ensure_active_org(
        self,
        session: Any,
        connection: FederationConnection,
    ) -> None:
        org = await _one_mapping(
            session,
            select(self._orgs).where(
                self._orgs.c.group_id == connection.organization_id
            ),
        )
        if org is None:
            now = utc_now()
            await session.execute(
                insert(self._orgs).values(
                    group_id=connection.organization_id,
                    name=connection.organization_name,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        if str(org["status"]) != "active":
            raise FederationBindingError("federation organization is not active")


async def _one_mapping(session: Any, statement: Any) -> Any | None:
    result = await session.execute(statement)
    return result.mappings().one_or_none()


def _external_identity_key(
    connection_id: str,
    issuer: str,
    external_subject: str,
) -> str:
    """Build a compact, case-sensitive key for one verified external identity."""
    digest = sha256()
    for value in (connection_id, issuer, external_subject):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def _validate_identity_row(
    identity_row: Any,
    *,
    connection_id: str,
    issuer: str,
    external_subject: str,
) -> None:
    persisted_identity = (
        str(identity_row["connection_id"]),
        str(identity_row["issuer"]),
        str(identity_row["external_subject"]),
    )
    requested_identity = (connection_id, issuer, external_subject)
    if persisted_identity != requested_identity:
        raise FederationBindingError("federated identity key collision")


def _validate_connection_row(
    connection: FederationConnection,
    row: Any | None,
) -> None:
    if row is None:
        raise FederationBindingError("federation connection is not persisted")
    expected = (
        connection.issuer,
        connection.organization_id,
        connection.default_role,
    )
    actual = (str(row["issuer"]), str(row["group_id"]), str(row["default_role"]))
    if actual != expected:
        raise FederationBindingError(
            "persisted federation connection does not match trusted configuration"
        )
    if str(row["status"]) != "active":
        raise FederationBindingError("federation connection is not active")


def _principal(
    connection_row: Any,
    user_row: Any,
    attributes: dict[str, Any] | None,
    roles: tuple[str, ...],
) -> LocalPrincipal:
    claims = attributes if isinstance(attributes, dict) else {}
    return LocalPrincipal(
        user_id=str(user_row["user_id"]),
        organization_id=str(connection_row["group_id"]),
        display_name=str(user_row["display_name"]),
        email=str(claims.get("email") or "") or None,
        roles=roles,
        auth_source=f"federated:{connection_row['connection_id']}",
    )


def _claim_values(value: Any) -> set[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return {normalized} if normalized else set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


__all__ = ["IdentityCenterFederatedIdentityStore"]
