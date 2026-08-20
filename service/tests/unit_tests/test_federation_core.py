# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Unit tests for the formal transport-neutral federation contracts."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from openjiuwen_runtime.service import (
    ExternalIdentity,
    FederatedIdentityStore,
    FederationAuthenticationResult,
    FederationBindingError,
    FederationConnection,
    FederationCoordinator,
    FederationError,
    FederationProvider,
    LocalPrincipal,
    UnknownFederationConnection,
)


def _connection() -> FederationConnection:
    return FederationConnection(
        connection_id="enterprise-a",
        issuer="https://idp.enterprise-a.example",
        organization_id="virtual-org-a",
        organization_name="Enterprise A",
    )


def _identity(*, issuer: str | None = None) -> ExternalIdentity:
    return ExternalIdentity(
        connection_id="enterprise-a",
        issuer=issuer or "https://idp.enterprise-a.example",
        external_subject="employee-10086",
        display_name="Enterprise Alice",
    )


class _Provider(FederationProvider):
    def __init__(self, identity: ExternalIdentity | None = None) -> None:
        self.identity = identity or _identity()

    async def begin_login(
        self,
        connection: FederationConnection,
        authorization_request_id: str,
    ) -> str:
        return f"https://idp.example/login?request={authorization_request_id}"

    async def consume_callback(
        self,
        connection: FederationConnection,
        parameters: Mapping[str, str],
    ) -> FederationAuthenticationResult:
        return FederationAuthenticationResult(
            authorization_request_id=parameters["authorization_request_id"],
            identity=self.identity,
        )


class _Store(FederatedIdentityStore):
    def __init__(self, principal: LocalPrincipal | None = None) -> None:
        self.resolve_count = 0
        self.closed = False
        self.principal = principal

    async def resolve_or_create(
        self,
        connection: FederationConnection,
        identity: ExternalIdentity,
    ) -> LocalPrincipal:
        self.validate_binding(connection, identity)
        self.resolve_count += 1
        return self.principal or LocalPrincipal(
            user_id="local-user-1",
            organization_id=connection.organization_id,
            display_name=identity.display_name,
            roles=(connection.default_role,),
        )

    async def find(
        self,
        *,
        connection_id: str,
        issuer: str,
        external_subject: str,
    ) -> LocalPrincipal | None:
        return None

    async def close(self) -> None:
        self.closed = True


def _coordinator(
    *,
    provider: FederationProvider | None = None,
    store: _Store | None = None,
) -> tuple[FederationCoordinator, _Store]:
    active_store = store or _Store()
    coordinator = FederationCoordinator(
        provider=provider or _Provider(),
        identity_store=active_store,
        connections={"enterprise-a": _connection()},
    )
    return coordinator, active_store


@pytest.mark.unit
async def test_coordinator_runs_provider_and_store_in_separate_steps():
    coordinator, store = _coordinator()

    login_url = await coordinator.begin_login("enterprise-a", "request-1")
    authentication = await coordinator.consume_callback(
        "enterprise-a",
        {"authorization_request_id": "request-1"},
    )

    assert login_url.endswith("request=request-1")
    assert authentication.authorization_request_id == "request-1"
    assert store.resolve_count == 0

    principal = await coordinator.resolve_or_create(
        "enterprise-a",
        authentication.identity,
    )

    assert principal.user_id == "local-user-1"
    assert principal.organization_id == "virtual-org-a"
    assert store.resolve_count == 1


@pytest.mark.unit
async def test_coordinator_rejects_unknown_connection_before_provider_call():
    coordinator, _ = _coordinator()

    with pytest.raises(UnknownFederationConnection, match="unknown"):
        await coordinator.begin_login("missing", "request-1")


@pytest.mark.unit
async def test_coordinator_rejects_untrusted_identity_before_store_write():
    coordinator, store = _coordinator(
        provider=_Provider(_identity(issuer="https://attacker.example")),
    )

    with pytest.raises(FederationBindingError, match="issuer"):
        await coordinator.consume_callback(
            "enterprise-a",
            {"authorization_request_id": "request-1"},
        )

    assert store.resolve_count == 0


@pytest.mark.unit
async def test_coordinator_rejects_principal_from_another_organization():
    store = _Store(
        LocalPrincipal(
            user_id="local-user-1",
            organization_id="another-organization",
            display_name="Enterprise Alice",
            roles=("member",),
        )
    )
    coordinator, _ = _coordinator(store=store)

    with pytest.raises(FederationBindingError, match="organization_id"):
        await coordinator.resolve_or_create("enterprise-a", _identity())


@pytest.mark.unit
async def test_coordinator_rejects_empty_authorization_request_id():
    coordinator, _ = _coordinator()

    with pytest.raises(FederationError, match="must not be empty"):
        await coordinator.begin_login("enterprise-a", "  ")


@pytest.mark.unit
async def test_coordinator_closes_owned_store_contract():
    coordinator, store = _coordinator()

    await coordinator.close()

    assert store.closed is True


@pytest.mark.unit
def test_coordinator_rejects_connection_mapping_key_mismatch():
    with pytest.raises(FederationError, match="mapping key"):
        FederationCoordinator(
            provider=_Provider(),
            identity_store=_Store(),
            connections={"alias": _connection()},
        )


@pytest.mark.unit
def test_coordinator_rejects_synchronous_provider_methods():
    def sync_begin_login():
        return "https://idp.example/login"

    def sync_consume_callback():
        return None

    provider = SimpleNamespace(
        begin_login=sync_begin_login,
        consume_callback=sync_consume_callback,
    )

    with pytest.raises(TypeError, match="begin_login must be declared with async def"):
        FederationCoordinator(
            provider=provider,
            identity_store=_Store(),
            connections={"enterprise-a": _connection()},
        )


@pytest.mark.unit
def test_coordinator_rejects_synchronous_store_methods():
    def sync_operation():
        return None

    store = SimpleNamespace(
        resolve_or_create=sync_operation,
        find=sync_operation,
        close=sync_operation,
    )

    with pytest.raises(
        TypeError, match="resolve_or_create must be declared with async def"
    ):
        FederationCoordinator(
            provider=_Provider(),
            identity_store=store,
            connections={"enterprise-a": _connection()},
        )
