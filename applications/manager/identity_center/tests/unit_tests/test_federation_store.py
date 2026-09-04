"""Unit tests for transactional federated-identity provisioning."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.exc import IntegrityError

from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.service import ExternalIdentity

from identity_center.core.federation import IdentityFederationService
from identity_center.core.federation.store import (
    IdentityCenterFederatedIdentityStore,
)
from identity_center.core.iam.services import OrgService, UserService
from identity_center.infrastructure.config import Settings
from identity_center.infrastructure.utils import utc_now
from identity_center.models.identity_models import (
    IDENTITY_USER_TABLE_DEF,
    FEDERATED_IDENTITY_TABLE_DEF,
    FEDERATION_LOGIN_CODE_TABLE_DEF,
    FEDERATION_ROLE_MAPPING_TABLE_DEF,
    IDENTITY_ORG_TABLE_DEF,
    IDENTITY_USER_ORG_MEMBERSHIP_TABLE_DEF,
)
from identity_center.models.table_init import init_all_tables
from identity_center.security.jwt_keys import load_signing_key


def _settings(db_path: str) -> Settings:
    return Settings(
        _env_file=None,
        IDENTITY_DB_TYPE="sqlite",
        IDENTITY_SQLITE_PATH=db_path,
        IDENTITY_FEDERATION_DEMO_ENABLED=True,
        IDENTITY_FEDERATION_PUBLIC_PATH_PREFIX="",
        IDENTITY_SEED_ADMIN=False,
        IDENTITY_SEED_USER1=False,
    )


async def _runtime(tmp_path):
    handler = SQLiteHandler(str(tmp_path / "identity.db"))
    await handler.init_database()
    await handler.connect()
    await init_all_tables(handler)
    await load_signing_key(handler)
    service = await IdentityFederationService.create(
        handler,
        _settings(str(tmp_path / "identity.db")),
    )
    return handler, service


def test_role_mapping_index_name_is_mysql_compatible():
    unique_index = FEDERATION_ROLE_MAPPING_TABLE_DEF.indexes[0]

    assert unique_index.name == "uq_federation_role_mapping_rule"
    assert len(unique_index.name) <= 64


def test_federated_identity_index_is_mysql_compatible():
    unique_index = FEDERATED_IDENTITY_TABLE_DEF.indexes[0]

    assert unique_index.columns == ["identity_key"]
    assert unique_index.unique is True
    assert unique_index.name == "uq_federated_identity_subject"
    assert len(unique_index.name) <= 64

    column_lengths = {
        column.name: column.length
        for column in FEDERATED_IDENTITY_TABLE_DEF.columns
    }
    max_key_bytes = 0
    for column_name in unique_index.columns:
        column_length = column_lengths[column_name]
        assert column_length is not None
        max_key_bytes += column_length * 4
    assert max_key_bytes <= 3072


def _query_value(url: str, name: str) -> str:
    values = parse_qs(urlsplit(url).query).get(name)
    assert values
    return values[0]


async def _federated_login(
    service: IdentityFederationService,
    *,
    employee_id: str,
    display_name: str,
    groups: str = "employees",
    extra_parameters: dict[str, str] | None = None,
):
    upstream = await service.begin_login("enterprise-demo", "/auth")
    request_id = _query_value(upstream, "authorization_request_id")
    parameters = {
        "authorization_request_id": request_id,
        "employee_id": employee_id,
        "display_name": display_name,
        "email": f"{employee_id}@example.test",
        "groups": groups,
    }
    parameters.update(extra_parameters or {})
    redirect, principal = await service.complete_callback(
        "enterprise-demo",
        parameters,
    )
    return _query_value(redirect, "federation_code"), principal


@pytest.mark.asyncio
async def test_first_login_is_idempotent_and_code_is_one_time(tmp_path):
    handler, service = await _runtime(tmp_path)
    try:
        code, first = await _federated_login(
            service,
            employee_id="employee-10086",
            display_name="Enterprise Alice",
        )

        tokens = await service.exchange_code(code)
        assert isinstance(tokens, dict)
        assert tokens["token_type"] == "bearer"
        assert await service.exchange_code(code) == "invalid_federation_code"

        _, repeated = await _federated_login(
            service,
            employee_id="employee-10086",
            display_name="Enterprise Alice Updated",
        )
        assert repeated.user_id == first.user_id
        assert repeated.organization_id == "federated-enterprise-demo"
        assert repeated.display_name == "Enterprise Alice Updated"
        assert repeated.roles == ("member",)

        assert await handler.count_records(IDENTITY_USER_TABLE_DEF.table_name, {}) == 1
        assert (
            await handler.count_records(FEDERATED_IDENTITY_TABLE_DEF.table_name, {})
            == 1
        )
        assert (
            await handler.count_records(IDENTITY_USER_ORG_MEMBERSHIP_TABLE_DEF.table_name, {})
            == 1
        )
        assert await handler.count_records(IDENTITY_ORG_TABLE_DEF.table_name, {}) == 1
        assert (
            await handler.count_records(FEDERATION_ROLE_MAPPING_TABLE_DEF.table_name, {})
            == 1
        )
        assert (
            await handler.count_records(FEDERATION_LOGIN_CODE_TABLE_DEF.table_name, {})
            == 1
        )
    finally:
        await service.close()
        await handler.disconnect()


@pytest.mark.asyncio
async def test_external_subject_matching_is_case_sensitive(tmp_path):
    handler, service = await _runtime(tmp_path)
    try:
        _, uppercase = await _federated_login(
            service,
            employee_id="Employee-Case",
            display_name="Uppercase Employee",
        )
        _, lowercase = await _federated_login(
            service,
            employee_id="employee-case",
            display_name="Lowercase Employee",
        )

        assert uppercase.user_id != lowercase.user_id
        assert (
            await handler.count_records(FEDERATED_IDENTITY_TABLE_DEF.table_name, {})
            == 2
        )
    finally:
        await service.close()
        await handler.disconnect()


@pytest.mark.asyncio
async def test_verified_group_grants_admin_and_next_login_can_revoke_it(tmp_path):
    handler, service = await _runtime(tmp_path)
    try:
        _, administrator = await _federated_login(
            service,
            employee_id="employee-admin",
            display_name="Enterprise Administrator",
            groups="employees, enterprise-admins",
        )
        assert administrator.roles == ("admin", "member")
        user = await handler.get(
            IDENTITY_USER_TABLE_DEF.table_name,
            {"user_id": administrator.user_id},
        )
        assert user is not None
        assert user.is_admin is True

        _, ordinary_user = await _federated_login(
            service,
            employee_id="employee-admin",
            display_name="Enterprise Administrator",
            groups="employees",
            extra_parameters={"is_admin": "true", "role": "admin"},
        )
        assert ordinary_user.user_id == administrator.user_id
        assert ordinary_user.roles == ("member",)
        user = await handler.get(
            IDENTITY_USER_TABLE_DEF.table_name,
            {"user_id": ordinary_user.user_id},
        )
        assert user is not None
        assert user.is_admin is False
    finally:
        await service.close()
        await handler.disconnect()


@pytest.mark.asyncio
async def test_role_mapping_reconciliation_is_idempotent_across_restarts(tmp_path):
    handler, service = await _runtime(tmp_path)
    try:
        before = await handler.list_records(
            FEDERATION_ROLE_MAPPING_TABLE_DEF.table_name,
            {},
            limit=10,
            offset=0,
        )
        assert len(before) == 1
        original_id = before[0].id
        original_created_at = before[0].created_at

        await service.close()
        service = await IdentityFederationService.create(
            handler,
            _settings(str(tmp_path / "identity.db")),
        )
        after = await handler.list_records(
            FEDERATION_ROLE_MAPPING_TABLE_DEF.table_name,
            {},
            limit=10,
            offset=0,
        )
        assert len(after) == 1
        assert after[0].id == original_id
        assert after[0].created_at == original_created_at
    finally:
        await service.close()
        await handler.disconnect()


@pytest.mark.asyncio
async def test_provisioning_failure_rolls_back_org_user_and_mapping(
    tmp_path,
    monkeypatch,
):
    handler, service = await _runtime(tmp_path)
    try:
        colliding_user_id = "fuser_collision"
        now = utc_now()
        await handler.create(
            IDENTITY_USER_TABLE_DEF.table_name,
            {
                "user_id": colliding_user_id,
                "display_name": "Existing User",
                "is_admin": False,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            },
        )

        class _FixedUuid:
            hex = "collision"

        monkeypatch.setattr(
            "identity_center.core.federation.store.uuid4",
            lambda: _FixedUuid(),
        )
        store = IdentityCenterFederatedIdentityStore(handler)
        with pytest.raises(IntegrityError):
            await store.resolve_or_create(
                service.connections[0],
                ExternalIdentity(
                    connection_id="enterprise-demo",
                    issuer="https://idp.enterprise-demo.example",
                    external_subject="employee-collision",
                    display_name="Should Roll Back",
                ),
            )

        assert await handler.count_records(IDENTITY_USER_TABLE_DEF.table_name, {}) == 1
        assert await handler.count_records(IDENTITY_ORG_TABLE_DEF.table_name, {}) == 0
        assert (
            await handler.count_records(FEDERATED_IDENTITY_TABLE_DEF.table_name, {})
            == 0
        )
        assert (
            await handler.count_records(IDENTITY_USER_ORG_MEMBERSHIP_TABLE_DEF.table_name, {})
            == 0
        )
    finally:
        await service.close()
        await handler.disconnect()


@pytest.mark.asyncio
async def test_iam_preserves_connection_org_and_cleans_deleted_virtual_user(
    tmp_path,
):
    handler, service = await _runtime(tmp_path)
    try:
        _, principal = await _federated_login(
            service,
            employee_id="employee-delete",
            display_name="Enterprise Delete Test",
        )

        with pytest.raises(ValueError, match="federation connection"):
            await OrgService(handler).delete("federated-enterprise-demo")

        assert await UserService(handler).delete(principal.user_id) is True
        assert await handler.count_records(IDENTITY_USER_TABLE_DEF.table_name, {}) == 0
        assert (
            await handler.count_records(FEDERATED_IDENTITY_TABLE_DEF.table_name, {})
            == 0
        )
        assert (
            await handler.count_records(IDENTITY_USER_ORG_MEMBERSHIP_TABLE_DEF.table_name, {})
            == 0
        )
        assert await handler.count_records(IDENTITY_ORG_TABLE_DEF.table_name, {}) == 1

        _, recreated = await _federated_login(
            service,
            employee_id="employee-delete",
            display_name="Enterprise Recreated User",
        )
        assert recreated.user_id != principal.user_id
    finally:
        await service.close()
        await handler.disconnect()
