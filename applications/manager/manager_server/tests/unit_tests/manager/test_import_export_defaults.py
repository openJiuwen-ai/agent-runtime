from __future__ import annotations

from copy import deepcopy

import pytest
from conftest import ManagerApiHarness

from manager_server.core.import_export.cluster import ClusterImportExportAdapter
from manager_server.core.import_export.registry import ImportExportContext
from manager_server.core.import_export.standalone import _manager_workbook
from manager_server.core.import_export.workbook import (
    SENSITIVE_REDACTED,
    dump_workbook,
    load_workbook_data,
)


@pytest.fixture(autouse=True)
def _no_runtime_push(monkeypatch):
    async def sync_runtime_config(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "manager_server.core.instance_resource.runtime_config_sync.sync_runtime_config",
        sync_runtime_config,
    )


def _restore_api_keys(workbook):
    for sheet in workbook.sheets:
        for row in sheet.rows:
            if row.get("api_key") == SENSITIVE_REDACTED:
                row["api_key"] = "test-import-key"
    return workbook


async def _import(http, resource_type, workbook):
    payload = dump_workbook(workbook)
    preflight = await http.post(f"/api/v1/import-export/{resource_type}/preflight", content=payload)
    assert preflight.status_code == 200, preflight.text
    report = preflight.json()["data"]
    assert report["can_import"] is True, report
    response = await http.post(
        f"/api/v1/import-export/{resource_type}/import",
        params={"confirmation_token": report["confirmation_token"]},
        content=payload,
    )
    assert response.status_code == 200, response.text
    return report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource_type,table,fields,expected",
    [
        (
            "model",
            "model_template",
            {
                "api_base": "https://models.example.com/v1",
                "api_key": "test-import-key",
                "model_id": "test-model",
                "model_provider": "OpenAI",
                "model_type": [],
                "retry_count": 0,
                "enabled": False,
                "enable_function_calling": False,
            },
            {
                "model_type": [],
                "timeout": 60,
                "retry_count": 0,
                "enabled": False,
                "enable_function_calling": False,
            },
        ),
        (
            "embedding",
            "embedding_template",
            {
                "api_base": "https://models.example.com/v1",
                "api_key": "test-import-key",
                "model_id": "test-embedding",
                "model_provider": "OpenAI",
            },
            {"client_config": {"timeout": 60, "retry_count": 3, "verify_ssl": True}},
        ),
        (
            "skill",
            "skill_prebuilt_template",
            {"skill_id": "test-skill", "package_url": "https://skills.example.com/skill.zip"},
            {"enabled": True},
        ),
        (
            "guardrail",
            "permissions_template",
            {"body": {}},
            {"body": {}, "enabled": True},
        ),
        (
            "extension",
            "extension_config_template",
            {
                "component": "gateway",
                "hook_type": "pre_request",
                "hook_config": {"handler": "test.handler"},
            },
            {
                "hook_config": {
                    "handler": "test.handler",
                    "params": None,
                    "schedule": None,
                    "data": None,
                }
            },
        ),
        (
            "mcp",
            "mcp_template",
            {
                "mcp_entry": {
                    "name": " test-mcp ",
                    "transport": " STDIO ",
                    "command": "python",
                    "enabled": False,
                }
            },
            {"mcp_entry": {"name": "test-mcp", "transport": "stdio", "command": "python"}},
        ),
        (
            "agent-template",
            "agent_template",
            {},
            {"template_ref": {}, "enabled": True},
        ),
        (
            "a2a-policy",
            "a2a_access_policy_template",
            {"policy_name": "Test policy", "mode": "denylist"},
            {"member_template_ids": [], "enabled": True},
        ),
        (
            "a2a-agent",
            "a2a_outbound_template",
            {
                "template_name": "A2A snapshot",
                "source_url": "https://agent.example.com",
                "card_path": "/.well-known/agent-card.json",
                "agent_card": {"name": "Test agent"},
                "card_fingerprint": "test-fingerprint",
                "selected_interface": {"url": "https://agent.example.com/rpc"},
            },
            {"card_revision": 1, "connect_timeout_seconds": 10.0, "enabled": True},
        ),
    ],
)
async def test_catalog_import_materializes_schema_defaults_and_round_trips(
    manager_api: ManagerApiHarness,
    resource_type,
    table,
    fields,
    expected,
):
    handler = manager_api.handler
    key = "policy_id" if resource_type == "a2a-policy" else "template_id"
    row = {key: f"import-{resource_type}", "template_name": " Test import ", **deepcopy(fields)}
    workbook = _restore_api_keys(
        _manager_workbook(
            resource_type=resource_type,
            resource_ids=[row[key]],
            label="Test import",
            rows_by_table={table: [row]},
            tables=(table,),
        )
    )
    await _import(manager_api.http, resource_type, workbook)
    stored = await handler.get(table, {key: row[key]})
    for field, value in expected.items():
        assert getattr(stored, field) == value
    if key == "template_id" and resource_type != "a2a-agent":
        assert stored.template_name == "Test import"

    # Export the stored snapshot, restore it, and verify that defaults do not
    # turn a subsequent import into a configuration conflict.
    exported = await manager_api.http.post(
        f"/api/v1/import-export/{resource_type}/export",
        json={"resource_ids": [row[key]]},
    )
    assert exported.status_code == 200, exported.text
    snapshot = _restore_api_keys(
        load_workbook_data(
            exported.content,
            expected_resource_type=resource_type,
        )
    )
    await handler.delete(table, {key: row[key]})
    await _import(manager_api.http, resource_type, snapshot)
    report = await _import(manager_api.http, resource_type, snapshot)
    assert report["summary"]["reuse"] == 1
    restored = await handler.get(table, {key: row[key]})
    for field, value in expected.items():
        assert getattr(restored, field) == value


@pytest.mark.asyncio
async def test_embedding_export_preserves_explicit_null_client_config(
    manager_api: ManagerApiHarness,
):
    fields = {
        "template_id": "null-client-config",
        "template_name": "Null config",
        "api_base": "https://models.example.com/v1",
        "api_key": "test-import-key",
        "model_id": "test-embedding",
        "model_provider": "OpenAI",
        "client_config": None,
    }
    workbook = _restore_api_keys(
        _manager_workbook(
            resource_type="embedding",
            resource_ids=[fields["template_id"]],
            label="Embedding",
            rows_by_table={"embedding_template": [fields]},
            tables=("embedding_template",),
        )
    )
    await _import(manager_api.http, "embedding", workbook)
    stored = await manager_api.handler.get(
        "embedding_template", {"template_id": fields["template_id"]}
    )
    assert stored.client_config is None


@pytest.mark.asyncio
async def test_cluster_import_restores_empty_model_type(manager_api: ManagerApiHarness):
    cluster_id = await manager_api.create_instance(name="import-defaults")
    adapter = ClusterImportExportAdapter()
    context = ImportExportContext(manager_api.handler)
    workbook = await adapter.export(context, cluster_id)
    model = next(sheet for sheet in workbook.sheets if sheet.name == "12_ModelTemplates")
    model.rows.append(
        {
            "template_id": "cluster-empty-type",
            "template_name": "Cluster model",
            "api_base": "https://models.example.com/v1",
            "api_key": "test-import-key",
            "model_id": "test-model",
            "model_provider": "OpenAI",
            "model_type": "",
        }
    )
    await _import(manager_api.http, "cluster", workbook)
    stored = await manager_api.handler.get("model_template", {"template_id": "cluster-empty-type"})
    assert stored.model_type == []


@pytest.mark.asyncio
async def test_cluster_preflight_rejects_invalid_catalog_configuration(
    manager_api: ManagerApiHarness,
):
    cluster_id = await manager_api.create_instance(name="invalid-import")
    workbook = await ClusterImportExportAdapter().export(
        ImportExportContext(manager_api.handler), cluster_id
    )
    model = next(sheet for sheet in workbook.sheets if sheet.name == "12_ModelTemplates")
    model.rows.append(
        {
            "template_id": "invalid-model",
            "template_name": "Invalid model",
            "api_base": "not-a-url",
            "api_key": "test-import-key",
            "model_id": "test-model",
            "model_provider": "OpenAI",
            "model_type": "",
        }
    )
    response = await manager_api.http.post(
        "/api/v1/import-export/cluster/preflight",
        content=dump_workbook(workbook),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["can_import"] is False
    assert any(
        action["action"] == "conflict" and action["object_type"] == "model_template"
        for action in response.json()["data"]["actions"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource_type,table,fields,missing",
    [
        ("guardrail", "permissions_template", {}, "body"),
        ("mcp", "mcp_template", {}, "mcp_entry"),
        (
            "a2a-agent",
            "a2a_outbound_template",
            {
                "source_url": "https://agent.example.com",
                "card_path": "/card.json",
                "agent_card": {},
                "selected_interface": {},
            },
            "card_fingerprint",
        ),
    ],
)
async def test_missing_required_catalog_fields_are_rejected_before_insert(
    manager_api: ManagerApiHarness,
    resource_type,
    table,
    fields,
    missing,
):
    row = {"template_id": "missing-required", "template_name": "Invalid import", **fields}
    workbook = _manager_workbook(
        resource_type=resource_type,
        resource_ids=[row["template_id"]],
        label="Invalid import",
        rows_by_table={table: [row]},
        tables=(table,),
    )
    payload = dump_workbook(workbook)
    response = await manager_api.http.post(
        f"/api/v1/import-export/{resource_type}/preflight",
        content=payload,
    )
    assert response.status_code == 200, response.text
    report = response.json()["data"]
    assert report["can_import"] is False
    assert any(missing in action["detail"] for action in report["actions"])
    response = await manager_api.http.post(
        f"/api/v1/import-export/{resource_type}/import",
        params={"confirmation_token": report["confirmation_token"]},
        content=payload,
    )
    assert response.status_code == 409, response.text
    assert await manager_api.handler.get(table, {"template_id": row["template_id"]}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["name", "image"])
async def test_cluster_preflight_rejects_missing_container_fields(
    manager_api: ManagerApiHarness,
    missing,
):
    cluster_id = await manager_api.create_instance(name="invalid-container-import")
    workbook = await ClusterImportExportAdapter().export(
        ImportExportContext(manager_api.handler), cluster_id
    )
    containers = next(sheet for sheet in workbook.sheets if sheet.name == "33_ContainerTemplates")
    row = {"container_id": "missing-container-field", "name": "agent", "image": "test-image"}
    row.pop(missing)
    containers.rows.append(row)
    response = await manager_api.http.post(
        "/api/v1/import-export/cluster/preflight",
        content=dump_workbook(workbook),
    )
    assert response.status_code == 200, response.text
    report = response.json()["data"]
    assert report["can_import"] is False
    assert any(
        action["object_type"] == "service_config_container" and missing in action["detail"]
        for action in report["actions"]
    )


@pytest.mark.asyncio
async def test_cluster_runtime_template_defaults_are_materialized(manager_api: ManagerApiHarness):
    cluster_id = await manager_api.create_instance(name="runtime-import-defaults")
    workbook = await ClusterImportExportAdapter().export(
        ImportExportContext(manager_api.handler), cluster_id
    )
    sheets = {sheet.name: sheet for sheet in workbook.sheets}
    sheets["33_ContainerTemplates"].rows.append(
        {
            "container_id": "import-container",
            "name": "agent",
            "image": "test-image",
        }
    )
    sheets["30_RuntimeTemplates"].rows.append(
        {
            "template_id": "import-runtime",
            "template_name": "Runtime import",
        }
    )
    sheets["31_RuntimeContainerBindings"].rows.append(
        {
            "runtime_template_id": "import-runtime",
            "role": "main",
            "container_id": "import-container",
        }
    )
    await _import(manager_api.http, "cluster", workbook)
    row = await manager_api.handler.get(
        "service_config_template", {"template_id": "import-runtime"}
    )
    assert row.namespace == "default"
    assert row.main_container_id == "import-container"
    assert row.sidecar_container_ids == []
    assert row.min_idle_pods == 0
    assert row.session_ttl == 60
    assert row.message_timeout == 600
    container = await manager_api.handler.get(
        "service_config_container", {"container_id": "import-container"}
    )
    assert container.image_pull_policy == "IfNotPresent"
    report = await _import(manager_api.http, "cluster", workbook)
    assert report["summary"].get("create", 0) == 0
