# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Dictionary-backed test implementation of the formal federation store."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from openjiuwen_runtime.service.auth.federation import (
    ExternalIdentity,
    FederatedIdentityStore,
    FederationConnection,
    LocalPrincipal,
)

IdentityKey = tuple[str, str, str]


@dataclass
class _MemoryUser:
    user_id: str
    display_name: str
    email: str | None


class InMemoryFederatedIdentityStore(FederatedIdentityStore):
    """Dictionary-backed implementation intended for unit tests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections: dict[str, FederationConnection] = {}
        self._identities: dict[IdentityKey, str] = {}
        self._users: dict[str, _MemoryUser] = {}
        self._memberships: dict[tuple[str, str], str] = {}

    async def resolve_or_create(
        self,
        connection: FederationConnection,
        identity: ExternalIdentity,
    ) -> LocalPrincipal:
        self.validate_binding(connection, identity)
        key = _identity_key(identity)

        async with self._lock:
            self._remember_connection(connection)
            user_id = self._identities.get(key)
            if user_id is None:
                user_id = f"user_{uuid4().hex}"
                self._identities[key] = user_id
                self._memberships[(connection.organization_id, user_id)] = (
                    connection.default_role
                )

            user = _MemoryUser(
                user_id=user_id,
                display_name=identity.display_name,
                email=identity.email,
            )
            self._users[user_id] = user
            role = self._memberships[(connection.organization_id, user_id)]
            return _principal(user, connection.organization_id, role)

    async def find(
        self,
        *,
        connection_id: str,
        issuer: str,
        external_subject: str,
    ) -> LocalPrincipal | None:
        key = (connection_id, issuer, external_subject)
        async with self._lock:
            user_id = self._identities.get(key)
            connection = self._connections.get(connection_id)
            if user_id is None or connection is None:
                return None
            user = self._users[user_id]
            role = self._memberships[(connection.organization_id, user_id)]
            return _principal(user, connection.organization_id, role)

    async def close(self) -> None:
        """The dictionary implementation owns no external resources."""

    def _remember_connection(self, connection: FederationConnection) -> None:
        existing = self._connections.get(connection.connection_id)
        if existing is not None and _connection_binding(
            existing
        ) != _connection_binding(connection):
            raise ValueError("connection_id is already bound to different settings")
        self._connections[connection.connection_id] = connection


def _identity_key(identity: ExternalIdentity) -> IdentityKey:
    return (
        identity.connection_id,
        identity.issuer,
        identity.external_subject,
    )


def _connection_binding(connection: FederationConnection) -> tuple[str, str, str]:
    return (
        connection.issuer,
        connection.organization_id,
        connection.default_role,
    )


def _principal(
    user: _MemoryUser,
    organization_id: str,
    role: str,
) -> LocalPrincipal:
    return LocalPrincipal(
        user_id=user.user_id,
        organization_id=organization_id,
        display_name=user.display_name,
        email=user.email,
        roles=(role,),
        auth_source="saml",
    )


__all__ = ["FederatedIdentityStore", "InMemoryFederatedIdentityStore"]
