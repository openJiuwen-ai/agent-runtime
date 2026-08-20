# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Transport-neutral orchestration for one federated identity provider."""

from __future__ import annotations

import inspect
from collections.abc import Mapping

from .domain import ExternalIdentity, FederationConnection, LocalPrincipal
from .errors import (
    FederationBindingError,
    FederationError,
    UnknownFederationConnection,
)
from .identity_store import FederatedIdentityStore
from .provider import FederationAuthenticationResult, FederationProvider


class FederationCoordinator:
    """Coordinate trusted connections, an async provider, and an identity store.

    OAuth2 authorization state, HTTP redirects, callback parsing, and token issuance
    stay in the host application. This class only validates connection boundaries and
    orchestrates the protocol-neutral provider/store operations.
    """

    def __init__(
        self,
        *,
        provider: FederationProvider,
        identity_store: FederatedIdentityStore,
        connections: Mapping[str, FederationConnection],
    ) -> None:
        _require_async_method(provider, "begin_login", role="federation provider")
        _require_async_method(
            provider,
            "consume_callback",
            role="federation provider",
        )
        _require_async_method(
            identity_store,
            "resolve_or_create",
            role="federated identity store",
        )
        _require_async_method(identity_store, "find", role="federated identity store")
        _require_async_method(identity_store, "close", role="federated identity store")

        normalized: dict[str, FederationConnection] = {}
        for key, connection in connections.items():
            connection_key = str(key).strip()
            if connection_key != connection.connection_id:
                raise FederationError(
                    "federation connection mapping key must match connection_id"
                )
            normalized[connection_key] = connection

        self._provider = provider
        self._identity_store = identity_store
        self._connections = normalized

    @property
    def connections(self) -> tuple[FederationConnection, ...]:
        """Return configured trusted connections in registration order."""
        return tuple(self._connections.values())

    def require_connection(self, connection_id: str) -> FederationConnection:
        """Return a trusted connection or raise a stable federation error."""
        normalized = str(connection_id or "").strip()
        connection = self._connections.get(normalized)
        if connection is None:
            raise UnknownFederationConnection(
                f"unknown federation connection: {normalized or '<empty>'}"
            )
        return connection

    async def begin_login(
        self,
        connection_id: str,
        authorization_request_id: str,
    ) -> str:
        """Start upstream authentication for a trusted connection."""
        request_id = str(authorization_request_id or "").strip()
        if not request_id:
            raise FederationError("authorization_request_id must not be empty")
        connection = self.require_connection(connection_id)
        login_url = await self._provider.begin_login(connection, request_id)
        if not str(login_url or "").strip():
            raise FederationError("federation provider returned an empty login URL")
        return login_url

    async def consume_callback(
        self,
        connection_id: str,
        parameters: Mapping[str, str],
    ) -> FederationAuthenticationResult:
        """Validate a provider callback without creating local identity records."""
        connection = self.require_connection(connection_id)
        result = await self._provider.consume_callback(connection, parameters)
        if not isinstance(result, FederationAuthenticationResult):
            raise FederationError(
                "federation provider returned an invalid authentication result"
            )
        if not str(result.authorization_request_id or "").strip():
            raise FederationError(
                "federation provider returned an empty authorization_request_id"
            )
        FederatedIdentityStore.validate_binding(connection, result.identity)
        return result

    async def resolve_or_create(
        self,
        connection_id: str,
        identity: ExternalIdentity,
    ) -> LocalPrincipal:
        """Resolve a validated identity into a stable local principal."""
        connection = self.require_connection(connection_id)
        FederatedIdentityStore.validate_binding(connection, identity)
        principal = await self._identity_store.resolve_or_create(connection, identity)
        if not isinstance(principal, LocalPrincipal):
            raise FederationError(
                "federated identity store returned an invalid local principal"
            )
        if principal.organization_id != connection.organization_id:
            raise FederationBindingError(
                "local principal organization_id does not match connection"
            )
        return principal

    async def close(self) -> None:
        """Close the configured identity store."""
        await self._identity_store.close()


def _require_async_method(instance: object, name: str, *, role: str) -> None:
    method = getattr(instance, name, None)
    if not callable(method) or not inspect.iscoroutinefunction(method):
        raise TypeError(f"{role}.{name} must be declared with async def")


__all__ = ["FederationCoordinator"]
