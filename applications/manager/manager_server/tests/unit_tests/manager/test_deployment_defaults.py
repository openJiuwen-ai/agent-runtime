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


async def _preview(manager_api):
    return await manager_api.get_json('/service-config-templates/deployment-defaults/preview')


def _restore_body(preview, index=0, **changes):
    chosen = preview['templates'][index]
    return {
        'fingerprint': preview['fingerprint'],
        'source_template_id': chosen['source_template_id'],
        'template': {**chosen['template'], **changes},
    }


@pytest.mark.asyncio
async def test_preview_reads_original_source_and_does_not_modify_catalog(manager_api, deployed):
    initial = await initialize_deployment_defaults(manager_api.handler)
    tid = initial['templates'][0]['template_id']
    original = await manager_api.handler.get('service_config_template', {'template_id': tid})
    await manager_api.handler.update('service_config_template', {'template_id': tid}, {'pod_concurrency': 999})
    await manager_api.handler.update('service_config_container', {'container_id': original.main_container_id}, {'image': 'damaged:image'})
    preview = await _preview(manager_api)
    assert preview['status'] == 'available'
    chosen = preview['templates'][0]
    assert chosen['source_template_id'] == 'default'
    assert chosen['template']['pod_concurrency'] == 200
    assert chosen['template']['namespace'] == 'zyq'
    assert chosen['containers'][0]['image'] == 'example/agent:v1'
    assert chosen['containers'][0]['container_id'].startswith('default-preview-')
    assert 'envFrom' not in chosen['containers'][0]
    assert 'scopes' not in chosen['template']['data']['config_sync']
    assert await manager_api.handler.count_records('service_config_template', {}) == 1
    assert await manager_api.handler.count_records('service_config_container', {}) == 1
    await manager_api.handler.delete('service_config_template', {'template_id': tid})
    await manager_api.handler.delete('service_config_container', {'container_id': original.main_container_id})
    assert (await _preview(manager_api))['templates'] == preview['templates']
    assert await manager_api.handler.count_records('service_config_container', {}) == 0


@pytest.mark.asyncio
async def test_restore_copies_original_containers_and_keeps_existing_records(manager_api, deployed):
    initial = await initialize_deployment_defaults(manager_api.handler)
    tid = initial['templates'][0]['template_id']
    original = await manager_api.handler.get('service_config_template', {'template_id': tid})
    cluster_id = await manager_api.create_instance()
    await manager_api.handler.create('instance_service_resource', {
        'resource_id': 'existing-default-binding', 'jiuwenclaw_id': cluster_id,
        'ref_template_id': tid, 'resource_name': 'existing runtime',
        'priority': 7, 'enabled': True, 'granted_by': 'admin',
        'created_at': utc_now(), 'updated_at': utc_now(),
    })
    await manager_api.handler.update('service_config_container', {'container_id': original.main_container_id}, {'image': 'custom:image'})
    preview = await _preview(manager_api)
    restored = await manager_api.post_json('/service-config-templates/deployment-defaults/restore', _restore_body(preview, template_name='恢复默认', pod_concurrency=50))
    assert restored['template_id'] != tid
    assert restored['main_container_id'] != original.main_container_id
    assert restored['main_container_id'].startswith('restore-')
    assert restored['main_image'] == 'example/agent:v1'
    assert restored['pod_concurrency'] == 50
    assert restored['namespace'] == 'zyq'
    assert restored['fs_group'] == 1000
    assert restored['node_name'] == 'node-1'
    assert restored['data']['deployment_default']['fingerprint'] == preview['fingerprint']
    old = await manager_api.handler.get('service_config_container', {'container_id': original.main_container_id})
    assert old.image == 'custom:image'
    saved = await manager_api.handler.get('service_config_template', {'template_id': restored['template_id']})
    _, containers = await service_template_wire(manager_api.handler, saved)
    assert containers[0]['envFrom'] == [{'secretRef': {'name': 'deploy-secret'}}]
    assert containers[0]['volumeMounts'] == [{'name': 'data', 'mountPath': '/data'}]
    binding = await manager_api.handler.get('instance_service_resource', {'resource_id': 'existing-default-binding'})
    assert binding.ref_template_id == tid
    assert binding.priority == 7
    assert await manager_api.handler.count_records('instance_service_resource', {}) == 1


@pytest.mark.asyncio
async def test_restore_after_delete_supports_multiple_source_templates_and_discards_unselected_containers(manager_api, deployed):
    payload = config()
    payload['rawdata']['containers'].append({'container_id': 'side', 'image': 'example/side:v1'})
    payload['rawdata']['templates'].append({**payload['rawdata']['templates'][0], 'template_id': 'second', 'sidecar_container_ids': ['side']})
    deployed.write_text(json.dumps(payload))
    preview = await _preview(manager_api)
    assert len(preview['templates']) == 2
    assert len(preview['templates'][1]['containers']) == 2
    restored = await manager_api.post_json('/service-config-templates/deployment-defaults/restore', _restore_body(preview, 1, sidecar_container_ids=[]))
    assert restored['data']['config_sync']['source_template_id'] == 'second'
    assert restored['sidecar_container_ids'] == []
    assert await manager_api.handler.count_records('service_config_template', {}) == 1
    assert await manager_api.handler.count_records('service_config_container', {}) == 1


@pytest.mark.asyncio
async def test_restore_allows_existing_container_selection_without_upsert(manager_api, deployed):
    initialized = await initialize_deployment_defaults(manager_api.handler)
    row = await manager_api.handler.get('service_config_template', {'template_id': initialized['templates'][0]['template_id']})
    await manager_api.handler.update('service_config_container', {'container_id': row.main_container_id}, {'image': 'custom:image'})
    preview = await _preview(manager_api)
    body = _restore_body(preview, main_container_id=row.main_container_id)
    body['template']['data']['config_sync']['containers'] = [{'container_id': row.main_container_id, 'image': 'injected:image'}]
    body['template']['data']['config_sync']['scopes'] = [{'scope_id': 'injected'}]
    body['template']['data']['custom'] = {'keep': True}
    restored = await manager_api.post_json('/service-config-templates/deployment-defaults/restore', body)
    assert restored['main_container_id'] == row.main_container_id
    assert restored['main_image'] == 'custom:image'
    assert restored['data']['custom'] == {'keep': True}
    assert 'scopes' not in restored['data']['config_sync']
    assert await manager_api.handler.count_records('service_config_container', {}) == 1


@pytest.mark.asyncio
async def test_restore_rejects_changed_source_without_writes(manager_api, deployed):
    preview = await _preview(manager_api)
    deployed.write_text(json.dumps(config('example/agent:v2')))
    response = await manager_api.http.post('/api/v1/service-config-templates/deployment-defaults/restore', json=_restore_body(preview))
    assert response.status_code == 409
    assert '重新读取' in response.json()['detail']
    assert await manager_api.handler.count_records('service_config_container', {}) == 0


@pytest.mark.asyncio
async def test_restore_omitted_bindings_copy_source_and_explicit_empty_values_are_preserved(manager_api, deployed):
    payload = config()
    payload['rawdata']['containers'].append({'container_id': 'side', 'image': 'example/side:v1'})
    payload['rawdata']['templates'][0].update(sidecar_container_ids=['side'], kubeconfig='/deployment/config')
    deployed.write_text(json.dumps(payload))
    preview = await _preview(manager_api)
    body = _restore_body(preview)
    body['template'] = {
        'template_name': '恢复后自行调整', 'description': '', 'node_name': '',
        'kubeconfig': '', 'volumes': [],
    }
    restored = await manager_api.post_json('/service-config-templates/deployment-defaults/restore', body)
    assert restored['main_container_id'].startswith('restore-')
    assert len(restored['sidecar_container_ids']) == 1
    assert restored['sidecar_container_ids'][0].startswith('restore-')
    assert restored['main_image'] == 'example/agent:v1'
    for field in ('description', 'node_name', 'kubeconfig'):
        assert restored[field] == ''
    assert restored['volumes'] == []
    assert restored['namespace'] == 'zyq'
    assert restored['pod_concurrency'] == 200
    assert await manager_api.handler.count_records('service_config_container', {}) == 2


@pytest.mark.asyncio
async def test_restore_invalid_selection_rolls_back_new_containers(manager_api, deployed):
    preview = await _preview(manager_api)
    body = _restore_body(preview, sidecar_container_ids=['nonexistent'])
    response = await manager_api.http.post('/api/v1/service-config-templates/deployment-defaults/restore', json=body)
    assert response.status_code == 400
    assert await manager_api.handler.count_records('service_config_container', {}) == 0
    assert await manager_api.handler.count_records('service_config_template', {}) == 0
    chosen = preview['templates'][0]['template']['main_container_id']
    for changes in [dict(main_container_id=''), dict(sidecar_container_ids=[chosen]), dict(main_container_id='default-preview-invalid')]:
        response = await manager_api.http.post('/api/v1/service-config-templates/deployment-defaults/restore', json=_restore_body(preview, **changes))
        assert response.status_code == 400
    assert await manager_api.handler.count_records('service_config_container', {}) == 0


@pytest.mark.asyncio
async def test_preview_unconfigured_and_missing_source_restore_are_explicit(manager_api, deployed, monkeypatch):
    preview = await _preview(manager_api)
    deployed.unlink()
    assert (await _preview(manager_api))['status'] == 'not_found'
    response = await manager_api.http.post('/api/v1/service-config-templates/deployment-defaults/restore', json=_restore_body(preview))
    assert response.status_code == 400
    monkeypatch.delenv('AGENTSERVER_JSON_PATH')
    assert (await _preview(manager_api))['status'] == 'unconfigured'
    assert await manager_api.handler.count_records('service_config_container', {}) == 0


@pytest.mark.asyncio
async def test_preview_and_restore_require_admin(manager_api, deployed):
    from fastapi import HTTPException
    from manager_server.routers.auth_guards import require_admin

    preview = await _preview(manager_api)
    async def deny():
        raise HTTPException(status_code=403, detail='admin required')
    manager_api.app.dependency_overrides[require_admin] = deny
    response = await manager_api.http.get('/api/v1/service-config-templates/deployment-defaults/preview')
    assert response.status_code == 403
    response = await manager_api.http.post('/api/v1/service-config-templates/deployment-defaults/restore', json=_restore_body(preview))
    assert response.status_code == 403
    assert await manager_api.handler.count_records('service_config_container', {}) == 0
