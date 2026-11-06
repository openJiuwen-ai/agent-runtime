"""Deployment defaults use actual DB rows and preserve already-bound revisions."""
import asyncio
import json

import pytest

from manager_server.core.template.deployment_defaults import (
    bootstrap_deployment_defaults,
    initialize_deployment_defaults,
)
from manager_server.core.instance_resource.runtime_config_sync import service_template_wire
from manager_server.core.instance_resource.runtime_config_sync import build_runtime_config
from manager_server.infrastructure.utils import utc_now


def config(image="example/agent:v1"):
    return {"type": "config_sync", "rawdata": {
        "containers": [{"container_id": "c-agent", "name": "agent", "image": image,
                        "imagePullPolicy": "IfNotPresent",
                        "ports": [{"containerPort": 8080}],
                        "envFrom": [{"secretRef": {"name": "deploy-secret"}}],
                        "securityContext": {"runAsUser": 1000},
                        "volumeMounts": [{"name": "data", "mountPath": "/data"}]}],
        "templates": [{"template_id": "default", "template_name": "default", "main_container_id": "c-agent",
                       "namespace": "zyq", "nodeName": "node-1", "fsGroup": 1000,
                       "sse_path": "/api/v1/events/stream", "volumes": [{"name": "data", "emptyDir": {}}],
                       "pod_concurrency": 200, "min_idle_pods": 1}],
        "scopes": [{"scope_id": "default", "template_id": "default", "routing_rules": ""}],
    }}


@pytest.fixture
def deployed(tmp_path, monkeypatch):
    path = tmp_path / "agentserver.json"
    path.write_text(json.dumps(config()))
    monkeypatch.setenv("AGENTSERVER_JSON_PATH", str(path))
    monkeypatch.delenv("MANAGER_DEPLOY_TOOL_DIR", raising=False)
    return path


@pytest.mark.asyncio
async def test_status_is_readonly_initialize_is_idempotent_and_preserves_wire(manager_api, deployed):
    status = await manager_api.get_json("/service-config-templates/deployment-defaults")
    assert status["status"] == "available"
    assert await manager_api.handler.count_records("service_config_template", {}) == 0
    first = await manager_api.post_json("/service-config-templates/deployment-defaults/initialize", {})
    second = await manager_api.post_json("/service-config-templates/deployment-defaults/initialize", {})
    assert first["status"] == "ready"
    assert first["created"] == 1
    assert second["created"] == 0
    assert first["templates"] == second["templates"]
    assert "config" not in first
    template = await manager_api.handler.get("service_config_template", {"template_id": first["templates"][0]["template_id"]})
    wire, containers = await service_template_wire(manager_api.handler, template)
    assert wire["nodeName"] == "node-1"
    assert wire["fsGroup"] == 1000
    assert wire["namespace"] == ""  # Runtime namespace inheritance contract.
    assert wire["volumes"] == config()["rawdata"]["templates"][0]["volumes"]
    assert containers[0]["envFrom"] == [{"secretRef": {"name": "deploy-secret"}}]
    assert containers[0]["securityContext"] == {"runAsUser": 1000}
    assert await manager_api.handler.count_records("instance_service_resource", {}) == 0
    cluster_id = await manager_api.create_instance()
    await manager_api.handler.create("instance_service_resource", {
        "resource_id": "deployment-binding", "jiuwenclaw_id": cluster_id,
        "ref_template_id": template.template_id, "resource_name": "deployment runtime",
        "priority": 0, "enabled": True, "granted_by": "admin",
        "created_at": utc_now(), "updated_at": utc_now(),
    })
    payload = await build_runtime_config(manager_api.handler, cluster_id)
    assert payload["templates"][0]["template_id"] == template.template_id
    assert payload["containers"][0]["container_id"] == template.main_container_id
    assert payload["scopes"][0]["scope_id"] != "default"
    assert payload["templates"][0]["pod_concurrency"] == 200


@pytest.mark.asyncio
async def test_new_deployment_does_not_overwrite_existing_or_custom_templates(manager_api, deployed):
    first = await initialize_deployment_defaults(manager_api.handler)
    old_id = first["templates"][0]["template_id"]
    old = await manager_api.handler.get("service_config_template", {"template_id": old_id})
    await manager_api.handler.update("service_config_template", {"template_id": old_id}, {"template_name": "my customization", "enabled": False})
    same = await initialize_deployment_defaults(manager_api.handler)
    assert same["templates"][0]["template_name"] == "my customization"
    assert same["templates"][0]["enabled"] is False
    deployed.write_text(json.dumps(config("example/agent:v2")))
    second = await initialize_deployment_defaults(manager_api.handler)
    assert second["templates"][0]["template_id"] != old_id
    assert await manager_api.handler.count_records("service_config_template", {}) == 2
    old_container = await manager_api.handler.get("service_config_container", {"container_id": old.main_container_id})
    assert old_container.image == "example/agent:v1"


@pytest.mark.asyncio
async def test_invalid_whole_file_has_no_partial_writes_and_never_leaks_secret(manager_api, deployed):
    bad = config()
    bad["rawdata"]["templates"].append({"template_id": "bad", "main_container_id": "missing"})
    deployed.write_text(json.dumps(bad))
    result = await initialize_deployment_defaults(manager_api.handler)
    assert result["status"] == "invalid"
    assert await manager_api.handler.count_records("service_config_container", {}) == 0
    bad = config()
    bad["rawdata"]["templates"][0]["pod_concurrency"] = "do-not-leak-secret"
    deployed.write_text(json.dumps(bad))
    result = await initialize_deployment_defaults(manager_api.handler)
    assert result["status"] == "invalid"
    assert "do-not-leak-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_missing_unconfigured_and_unrendered_sources_are_explicit(manager_api, deployed, monkeypatch):
    deployed.unlink()
    assert (await initialize_deployment_defaults(manager_api.handler))["status"] == "not_found"
    monkeypatch.delenv("AGENTSERVER_JSON_PATH")
    assert (await initialize_deployment_defaults(manager_api.handler))["status"] == "unconfigured"
    monkeypatch.setenv("AGENTSERVER_JSON_PATH", str(deployed))
    deployed.write_text(json.dumps(config("<<AGENT_SERVER_IMAGE>>")))
    assert (await initialize_deployment_defaults(manager_api.handler))["status"] == "invalid"
    bad = config()
    bad["rawdata"]["templates"][0]["runtimeClassName"] = "kata"
    deployed.write_text(json.dumps(bad))
    result = await initialize_deployment_defaults(manager_api.handler)
    assert result["status"] == "invalid"
    assert "runtimeClassName" in result["message"]


@pytest.mark.asyncio
async def test_startup_and_concurrent_retry_do_not_duplicate_rows(manager_api, deployed):
    await asyncio.gather(*[initialize_deployment_defaults(manager_api.handler) for _ in range(3)])
    await bootstrap_deployment_defaults(manager_api.handler)
    assert await manager_api.handler.count_records("service_config_template", {}) == 1
    assert await manager_api.handler.count_records("service_config_container", {}) == 1


@pytest.mark.asyncio
async def test_multiple_templates_created_and_deleted_container_repaired(manager_api, deployed):
    payload = config()
    payload["rawdata"]["templates"].append({**payload["rawdata"]["templates"][0], "template_id": "second"})
    deployed.write_text(json.dumps(payload))
    result = await initialize_deployment_defaults(manager_api.handler)
    assert result["created"] == 2
    assert len(result["templates"]) == 2
    row = await manager_api.handler.get("service_config_template", {"template_id": result["templates"][0]["template_id"]})
    await manager_api.handler.delete("service_config_container", {"container_id": row.main_container_id})
    assert (await manager_api.get_json("/service-config-templates/deployment-defaults"))["status"] == "available"
    assert (await initialize_deployment_defaults(manager_api.handler))["status"] == "ready"


@pytest.mark.asyncio
async def test_startup_failure_does_not_prevent_manager_start(manager_api, deployed, monkeypatch):
    async def broken(_handler):
        raise RuntimeError("private-db-details")
    monkeypatch.setattr("manager_server.core.template.deployment_defaults.initialize_deployment_defaults", broken)
    await bootstrap_deployment_defaults(manager_api.handler)


@pytest.mark.asyncio
async def test_deployment_endpoints_require_admin(manager_api, deployed):
    from fastapi import HTTPException
    from manager_server.routers.auth_guards import require_admin

    async def deny():
        raise HTTPException(status_code=403, detail="admin required")
    manager_api.app.dependency_overrides[require_admin] = deny
    for method, suffix in [("GET", ""), ("POST", "/initialize")]:
        response = await manager_api.http.request(method, f"/api/v1/service-config-templates/deployment-defaults{suffix}")
        assert response.status_code == 403
    assert await manager_api.handler.count_records("service_config_template", {}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("section,key", [("containers", "unexpectedSetting"), ("templates", "unexpectedSetting"), ("templates", "node_name")])
async def test_unsupported_or_ambiguous_fields_fail_before_writes(manager_api, deployed, section, key):
    payload = config()
    payload["rawdata"][section][0][key] = "private-value"
    deployed.write_text(json.dumps(payload))
    result = await initialize_deployment_defaults(manager_api.handler)
    assert result["status"] == "invalid"
    assert "private-value" not in json.dumps(result)
    assert await manager_api.handler.count_records("service_config_container", {}) == 0


@pytest.mark.asyncio
async def test_database_failure_rolls_back_all_template_rows(manager_api, deployed, monkeypatch):
    original = manager_api.handler.get_table
    def fail_template_table(name):
        if name == "service_config_template":
            raise RuntimeError("synthetic write failure")
        return original(name)
    monkeypatch.setattr(manager_api.handler, "get_table", fail_template_table)
    with pytest.raises(RuntimeError, match="synthetic write failure"):
        await initialize_deployment_defaults(manager_api.handler)
    assert await manager_api.handler.count_records("service_config_container", {}) == 0
    assert await manager_api.handler.count_records("service_config_template", {}) == 0
