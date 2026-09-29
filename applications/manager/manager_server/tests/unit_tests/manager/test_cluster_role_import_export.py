from __future__ import annotations

import pytest
from conftest import ManagerApiHarness

from manager_server.core.authz import AuthzService
from manager_server.core.import_export.cluster import ClusterImportExportAdapter
from manager_server.core.import_export.registry import ImportExportContext, WorkbookData
from manager_server.core.import_export.workbook import dump_workbook, load_workbook_data
from manager_server.models.authz_models import AUTHZ_ROLE_USER_TABLE_DEF


def _sheet_map(workbook: WorkbookData):
    return {sheet.name: sheet for sheet in workbook.sheets}


def _without_role_sheets(workbook: WorkbookData) -> WorkbookData:
    return WorkbookData(
        resource_type=workbook.resource_type,
        resource_id=workbook.resource_id,
        resource_name=workbook.resource_name,
        sheets=[sheet for sheet in workbook.sheets if not sheet.name.startswith("4")],
        format_version=workbook.format_version,
        metadata=dict(workbook.metadata),
    )


async def _prepare_cluster_role_fixture(
    manager_api: ManagerApiHarness,
    monkeypatch: pytest.MonkeyPatch,
):
    user_id = "cluster-direct-user"
    unrelated_user_id = "unrelated-role-user"

    async def fake_identity_request(
        method: str,
        path: str,
        _authorization: str | None,
        body: dict | None = None,
    ):
        assert method == "GET"
        assert body is None
        if path == f"/v1/users/{user_id}":
            return {
                "user_id": user_id,
                "username": user_id,
                "identity_provider": "local",
                "display_name": "Cluster Direct User",
                "status": "active",
            }
        raise AssertionError(f"unexpected identity request: {path}")

    monkeypatch.setattr(
        "manager_server.core.import_export.cluster._identity_request",
        fake_identity_request,
    )
    cluster_id = await manager_api.create_instance(name="cluster-role-roundtrip")
    grant = await manager_api.http.post(
        f"/api/v1/instances/{cluster_id}/users",
        json={"ids": [user_id]},
    )
    assert grant.status_code == 200, grant.text

    authz = AuthzService(manager_api.handler)
    role = await authz.create_role(
        role_id="cluster_reader",
        name="Cluster Reader",
        description="Role dependency round trip",
        scope="user",
        permission_ids=[],
        operator_id="test-operator",
    )
    await authz.assign_role_users(
        role["role_id"],
        [user_id, unrelated_user_id],
        "test-operator",
    )
    return cluster_id, user_id, unrelated_user_id, role


@pytest.mark.asyncio
async def test_cluster_export_contains_only_direct_user_role_closure(
    manager_api: ManagerApiHarness,
    monkeypatch: pytest.MonkeyPatch,
):
    cluster_id, user_id, unrelated_user_id, role = await _prepare_cluster_role_fixture(
        manager_api,
        monkeypatch,
    )
    adapter = ClusterImportExportAdapter()
    workbook = await adapter.export(
        ImportExportContext(manager_api.handler, "Bearer test"),
        cluster_id,
    )
    loaded = load_workbook_data(dump_workbook(workbook), expected_resource_type="cluster")
    sheets = _sheet_map(loaded)

    assert sheets["40_Roles"].rows == [
        {
            "role_id": role["role_id"],
            "name": role["name"],
            "description": role["description"],
            "scope": role["scope"],
            "is_system": role["is_system"],
            "enabled": role["enabled"],
        }
    ]
    assert sheets["41_RolePermissions"].rows == []
    assert sheets["42_RoleAssignments"].rows == [
        {"role_id": role["role_id"], "user_id": user_id, "expires_at": None}
    ]
    assert unrelated_user_id not in {
        str(item.get("user_id") or "") for item in sheets["42_RoleAssignments"].rows
    }


@pytest.mark.asyncio
async def test_cluster_import_restores_role_and_assignment(
    manager_api: ManagerApiHarness,
    monkeypatch: pytest.MonkeyPatch,
):
    cluster_id, user_id, _, role = await _prepare_cluster_role_fixture(
        manager_api,
        monkeypatch,
    )
    authz = AuthzService(manager_api.handler)
    adapter = ClusterImportExportAdapter()
    context = ImportExportContext(
        manager_api.handler,
        "Bearer test",
        operator_id="test-operator",
    )
    workbook = await adapter.export(context, cluster_id)
    assert await authz.delete_role(role["role_id"], "test-operator") is True

    report = await adapter.preflight(context, workbook)
    result = await adapter.apply(context, workbook)

    assert report["can_import"] is True
    assert report["summary"]["create"] == 2
    assert result["created_manager_objects"] == 2
    restored = await authz.get_role(role["role_id"])
    assert restored is not None
    assert restored["name"] == role["name"]
    assignment = await manager_api.handler.get(
        AUTHZ_ROLE_USER_TABLE_DEF.table_name,
        {"role_id": role["role_id"], "user_id": user_id},
    )
    assert assignment is not None


@pytest.mark.asyncio
async def test_cluster_import_accepts_legacy_workbook_without_role_sheets(
    manager_api: ManagerApiHarness,
    monkeypatch: pytest.MonkeyPatch,
):
    cluster_id, _, _, _ = await _prepare_cluster_role_fixture(manager_api, monkeypatch)
    adapter = ClusterImportExportAdapter()
    context = ImportExportContext(manager_api.handler, "Bearer test")
    workbook = _without_role_sheets(await adapter.export(context, cluster_id))

    report = await adapter.preflight(context, workbook)

    assert report["can_import"] is True
    assert not any(
        str(item.get("object_type") or "").startswith("authz_") for item in report["actions"]
    )


@pytest.mark.asyncio
async def test_cluster_import_rejects_partial_role_sheet_set(
    manager_api: ManagerApiHarness,
    monkeypatch: pytest.MonkeyPatch,
):
    cluster_id, _, _, _ = await _prepare_cluster_role_fixture(manager_api, monkeypatch)
    adapter = ClusterImportExportAdapter()
    context = ImportExportContext(manager_api.handler, "Bearer test")
    workbook = await adapter.export(context, cluster_id)
    workbook.sheets = [sheet for sheet in workbook.sheets if sheet.name != "42_RoleAssignments"]

    with pytest.raises(ValueError, match="incomplete role sheet set"):
        await adapter.preflight(context, workbook)
