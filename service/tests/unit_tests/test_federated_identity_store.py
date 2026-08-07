# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Unit tests for dictionary and SQLite federated identity stores."""

import pytest

from examples.federated_auth import (
    ExternalIdentity,
    FederationConnection,
    InMemoryFederatedIdentityStore,
)


def _connection(
    connection_id: str = "enterprise-a",
    issuer: str = "https://idp.enterprise-a.example",
    organization_id: str = "virtual-org-a",
) -> FederationConnection:
    return FederationConnection(
        connection_id=connection_id,
        issuer=issuer,
        organization_id=organization_id,
        organization_name=f"Organization {organization_id}",
    )


def _identity(
    external_subject: str = "employee-10086",
    connection_id: str = "enterprise-a",
    issuer: str = "https://idp.enterprise-a.example",
    display_name: str = "Alice",
) -> ExternalIdentity:
    return ExternalIdentity(
        connection_id=connection_id,
        issuer=issuer,
        external_subject=external_subject,
        display_name=display_name,
        email="alice@enterprise-a.example",
        attributes={"department": "research"},
    )


@pytest.mark.unit
async def test_in_memory_store_reuses_user_and_refreshes_profile():
    store = InMemoryFederatedIdentityStore()
    connection = _connection()

    first = await store.resolve_or_create(connection, _identity())
    second = await store.resolve_or_create(
        connection,
        _identity(display_name="Alice Updated"),
    )

    assert first.user_id == second.user_id
    assert second.organization_id == "virtual-org-a"
    assert second.display_name == "Alice Updated"
    assert second.roles == ("member",)


@pytest.mark.unit
async def test_in_memory_store_isolates_connections_with_same_subject():
    store = InMemoryFederatedIdentityStore()
    first = await store.resolve_or_create(_connection(), _identity())
    second = await store.resolve_or_create(
        _connection(
            connection_id="enterprise-b",
            issuer="https://idp.enterprise-b.example",
            organization_id="virtual-org-b",
        ),
        _identity(
            connection_id="enterprise-b",
            issuer="https://idp.enterprise-b.example",
        ),
    )

    assert first.user_id != second.user_id
    assert second.organization_id == "virtual-org-b"


@pytest.mark.unit
async def test_in_memory_store_allows_organization_display_name_update():
    store = InMemoryFederatedIdentityStore()
    connection = _connection()
    first = await store.resolve_or_create(connection, _identity())

    renamed = connection.model_copy(update={"organization_name": "Renamed"})
    second = await store.resolve_or_create(renamed, _identity())

    assert second.user_id == first.user_id


@pytest.mark.unit
async def test_store_rejects_untrusted_identity_binding():
    store = InMemoryFederatedIdentityStore()
    with pytest.raises(ValueError, match="issuer"):
        await store.resolve_or_create(
            _connection(),
            _identity(issuer="https://untrusted-idp.example"),
        )
