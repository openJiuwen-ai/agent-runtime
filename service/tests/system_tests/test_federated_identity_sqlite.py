# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SQLite integration tests for the federated identity example."""

import asyncio

import pytest

from examples.federated_auth import (
    DatabaseFederatedIdentityStore,
    ExternalIdentity,
    FederationConnection,
)


def _connection() -> FederationConnection:
    return FederationConnection(
        connection_id="enterprise-a",
        issuer="https://idp.enterprise-a.example",
        organization_id="virtual-org-a",
        organization_name="Enterprise A",
    )


def _identity() -> ExternalIdentity:
    return ExternalIdentity(
        connection_id="enterprise-a",
        issuer="https://idp.enterprise-a.example",
        external_subject="employee-10086",
        display_name="Alice",
        email="alice@enterprise-a.example",
        attributes={"department": "research"},
    )


@pytest.mark.integration
async def test_sqlite_store_persists_identity_across_reopen(tmp_path):
    database_path = tmp_path / "federated-auth.db"
    connection = _connection()
    identity = _identity()

    first_store = DatabaseFederatedIdentityStore(database_path)
    first = await first_store.resolve_or_create(connection, identity)
    await first_store.close()

    second_store = DatabaseFederatedIdentityStore(database_path)
    second = await second_store.find(
        connection_id=identity.connection_id,
        issuer=identity.issuer,
        external_subject=identity.external_subject,
    )
    await second_store.close()

    assert second is not None
    assert second.user_id == first.user_id
    assert second.organization_id == first.organization_id


@pytest.mark.integration
async def test_sqlite_store_concurrent_first_login_creates_one_user(tmp_path):
    store = DatabaseFederatedIdentityStore(tmp_path / "federated-auth.db")
    connection = _connection()
    identity = _identity()

    principals = await asyncio.gather(
        *(store.resolve_or_create(connection, identity) for _ in range(20))
    )
    await store.close()

    assert len({principal.user_id for principal in principals}) == 1


@pytest.mark.integration
async def test_sqlite_store_rejects_connection_rebinding(tmp_path):
    store = DatabaseFederatedIdentityStore(tmp_path / "federated-auth.db")
    connection = _connection()
    await store.resolve_or_create(connection, _identity())

    rebound = connection.model_copy(update={"organization_id": "other-org"})
    with pytest.raises(ValueError, match="different settings"):
        await store.resolve_or_create(rebound, _identity())
    await store.close()
