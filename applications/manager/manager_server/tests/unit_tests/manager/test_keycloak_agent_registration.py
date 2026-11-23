"""Keycloak registration protocol, permissions and template credential delivery."""

import asyncio
import json
import threading
from pathlib import Path
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import HTTPException

from manager_server.core.template import push_template_to_gateway as push
from manager_server.core.agent_definition import keycloak_client as clients
from manager_server.core.agent_definition.service import AgentDefinitionService
from manager_server.infrastructure.utils import new_uuid4, utc_now
from manager_server.infrastructure.config import Settings
from manager_server.integrations.keycloak.agent_clients import (
    KeycloakAgentClients, RegistrationConfig, RegistrationError, load_registration_config,
)
from manager_server.integrations.keycloak import agent_clients
from manager_server.routers import agent_client_registration_routers as routes
from manager_server.routers.auth_guards import require_admin

pytestmark = pytest.mark.unit
TEMPLATE_ID = "8bbff4f1-dfa3-48d6-84e0-8dd3368c01f1"
CLIENT_ID = "custom-agent-client"
CONFIG = {
    "admin_base_url": "https://sso.example.com/admin/realms/company",
    "token_url": "https://sso.example.com/realms/company/protocol/openid-connect/token",
    "management_client_id": "studio-backend", "management_client_secret": "management-secret",
    "agent_token_audience": "tools-gateway",
}
PATH = "/api/v1/integrations/keycloak/agent-client-registrations"


class KeycloakSimulator:
    def __init__(self):
        self.expected_client_id = CLIENT_ID
        self.queried_client_ids = []
        self.client = None
        self.mappers = []
        self.created = 0
        self.deleted = 0
        self.probes = 0
        self.mapper_failure = False
        self.mapper_conflict = False
        self.lookup_failure = None

    def handle(self, request):
        path, method = request.url.path, request.method
        if path.endswith("/token"):
            form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
            assert form["grant_type"] == "client_credentials"
            if form["client_id"] == "studio-backend":
                assert form["client_secret"] == "management-secret"
                return httpx.Response(200, json={"access_token": "admin-token"})
            assert form["client_id"] == self.client["clientId"]
            assert form["client_secret"] == "generated-secret"
            assert request.headers.get("Authorization") != "Bearer admin-token"
            self.probes += 1
            return httpx.Response(200, json={"access_token": "actor-token", "token_type": "Bearer"})
        assert request.headers["Authorization"] == "Bearer admin-token"
        if path.endswith("/clients"):
            if method == "GET":
                self.queried_client_ids.append(request.url.params["clientId"])
                assert request.url.params["clientId"] == self.expected_client_id
                if self.created and self.lookup_failure == "http":
                    return httpx.Response(503)
                if self.created and self.lookup_failure == "invalid_json":
                    return httpx.Response(200, text="not-json")
                if self.created and self.lookup_failure == "not_found":
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json=[self.client] if self.client else [])
            self.created += 1
            body = json.loads(request.content)
            assert body["clientId"] == self.expected_client_id
            assert body["publicClient"] is False and body["serviceAccountsEnabled"] is True
            assert body["standardFlowEnabled"] is False
            self.client = {**body, "id": "internal-client-uuid"}
            location = request.url.join("clients/internal-client-uuid")
            return httpx.Response(201, headers={"Location": str(location)})
        if path.endswith("/protocol-mappers/models"):
            if method == "GET":
                return httpx.Response(200, json=self.mappers)
            if self.mapper_failure:
                return httpx.Response(403, json={"error": "secret-must-not-leak"})
            self.mappers.append(json.loads(request.content))
            return httpx.Response(409 if self.mapper_conflict else 201)
        if path.endswith("/client-secret"):
            assert method == "GET"  # Never regenerate an existing secret on a retry.
            return httpx.Response(200, json={"type": "secret", "value": "generated-secret"})
        if path.endswith("/internal-client-uuid") and method == "GET":
            return httpx.Response(200, json=self.client) if self.client else httpx.Response(404)
        if method == "DELETE":
            self.deleted += 1
            self.client = None
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {method} {path}")


@pytest.fixture
def keycloak_service():
    simulator = KeycloakSimulator()
    service = KeycloakAgentClients(RegistrationConfig(**CONFIG), transport=httpx.MockTransport(simulator.handle))
    return service, simulator


def test_disabled_feature_does_not_read_configuration(tmp_path):
    settings = Settings(MANAGER_AGENT_CLIENT_REGISTRATION_ENABLED=False,
                        MANAGER_AGENT_CLIENT_REGISTRATION_CONFIG_FILE=str(tmp_path / "missing.json"))
    with pytest.raises(RegistrationError, match="未启用"):
        load_registration_config(settings)


def test_enabled_feature_loads_separate_configuration(tmp_path):
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(CONFIG))
    settings = Settings(MANAGER_AGENT_CLIENT_REGISTRATION_ENABLED=True,
                        MANAGER_AGENT_CLIENT_REGISTRATION_CONFIG_FILE=str(path))
    assert load_registration_config(settings).management_client_id == "studio-backend"


def test_invalid_ca_is_reported_without_exposing_configuration(tmp_path):
    ca_file = tmp_path / "invalid-ca.pem"
    ca_file.write_text("not a certificate", encoding="utf-8")
    path = tmp_path / "registration.json"
    path.write_text(json.dumps({**CONFIG, "ca_bundle": str(ca_file)}), encoding="utf-8")
    settings = Settings(MANAGER_AGENT_CLIENT_REGISTRATION_ENABLED=True,
                        MANAGER_AGENT_CLIENT_REGISTRATION_CONFIG_FILE=str(path))
    with pytest.raises(RegistrationError, match="配置不可用") as error:
        load_registration_config(settings)
    assert "management-secret" not in str(error.value)


@pytest.mark.parametrize("changes", [
    {"token_url": "http://sso.example.com/token"},
    {"admin_base_url": "https://user:password@sso.example.com/admin"},
    {"management_client_secret": ""},
    {"agent_token_audience": ""},
])
def test_invalid_registration_configuration(changes):
    with pytest.raises(ValueError):
        RegistrationConfig(**{**CONFIG, **changes})


@pytest.mark.asyncio
async def test_registration_retry_reuses_client_and_secret(keycloak_service):
    service, simulator = keycloak_service
    first = await service.register(TEMPLATE_ID, CLIENT_ID, "My Agent")
    second = await service.register(TEMPLATE_ID, CLIENT_ID, "Renamed Agent")
    assert first == second
    assert first["client_secret"] == "generated-secret"
    assert simulator.queried_client_ids[0] == CLIENT_ID
    assert simulator.created == 1 and len(simulator.mappers) == 1 and simulator.probes == 2
    assert simulator.mappers[0]["config"]["included.custom.audience"] == "tools-gateway"


@pytest.mark.asyncio
async def test_failed_registration_rolls_back_owned_client_without_exposing_body(keycloak_service):
    service, simulator = keycloak_service
    simulator.mapper_failure = True
    with pytest.raises(RegistrationError, match="配置 Agent 受众失败：Keycloak 返回 HTTP 403") as error:
        await service.register(TEMPLATE_ID, CLIENT_ID, "My Agent")
    assert "secret-must-not-leak" not in str(error.value)
    assert simulator.client is None and simulator.deleted == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "invalid_json", "not_found"])
async def test_lookup_failure_after_creation_rolls_back_using_location(keycloak_service, failure):
    service, simulator = keycloak_service
    simulator.lookup_failure = failure
    with pytest.raises(RegistrationError):
        await service.register(TEMPLATE_ID, CLIENT_ID, "My Agent")
    assert simulator.created == 1
    assert simulator.deleted == 1
    assert simulator.client is None


@pytest.mark.asyncio
async def test_overlapping_mapper_creation_accepts_matching_existing_audience(keycloak_service):
    service, simulator = keycloak_service
    simulator.mapper_conflict = True
    result = await service.register(TEMPLATE_ID, CLIENT_ID, "My Agent")
    assert result["client_secret"] == "generated-secret"
    assert simulator.deleted == 0 and len(simulator.mappers) == 1


@pytest.mark.asyncio
async def test_cannot_adopt_or_delete_another_templates_client(keycloak_service):
    service, simulator = keycloak_service
    binding = await service.register(TEMPLATE_ID, CLIENT_ID, "My Agent")
    with pytest.raises(RegistrationError, match="already in use"):
        await service.register("other-template", CLIENT_ID, "Other Agent")
    with pytest.raises(RegistrationError, match="already in use"):
        await service.delete("other-template", binding)
    assert simulator.deleted == 0


@pytest.mark.asyncio
async def test_owned_client_can_be_deleted_idempotently(keycloak_service):
    service, simulator = keycloak_service
    binding = await service.register(TEMPLATE_ID, CLIENT_ID, "My Agent")
    await service.delete(TEMPLATE_ID, binding)
    await service.delete(TEMPLATE_ID, binding)
    assert simulator.deleted == 1


@pytest.mark.asyncio
async def test_feature_off_and_invalid_configuration_do_not_break_manager(manager_api, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", False)
    monkeypatch.setattr(routes.settings, "agent_client_registration_config_file", str(tmp_path / "missing.json"))
    response = await manager_api.http.get(f"{PATH}/capabilities")
    assert response.json() == {"enabled": False, "configured": False}
    for method in ("GET", "POST", "DELETE"):
        response = await manager_api.http.request(method, f"/api/v1/agent-definitions/{TEMPLATE_ID}/keycloak-client")
        assert response.status_code == 404
    response = await manager_api.http.post(
        f"/api/v1/agent-templates/{TEMPLATE_ID}/keycloak-client", json={"client_id": CLIENT_ID},
    )
    assert response.status_code == 404
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", True)
    response = await manager_api.http.get(f"{PATH}/capabilities")
    assert response.json()["configured"] is False
    assert (await manager_api.http.get("/api/v1/agent-templates/")).status_code == 200


@pytest.fixture
def enabled_registration(monkeypatch, keycloak_service):
    service, simulator = keycloak_service
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", True)
    monkeypatch.setattr(clients, "load_registration_service", lambda _: service)
    return service, simulator


def _reference_gateway(monkeypatch):
    async def references(*_args):
        return {"gateway-1"}

    monkeypatch.setattr(push, "_referencing_reachable_jids", references)
    monkeypatch.setattr(push, "collect_referenced_jiuwenclaw_ids_for_template", references)


async def _create_template(http, **fields):
    response = await http.post("/api/v1/agent-templates/", json={"template_name": "Agent", **fields})
    assert response.status_code == 200
    template_id = response.json()["data"]["template_id"]
    return f"/api/v1/agent-templates/{template_id}", template_id


async def _create_agent(manager_api, *, data=None):
    path, config_id = await _create_template(manager_api.http, data=data)
    runtime_id = new_uuid4()
    now = utc_now()
    await manager_api.handler.create("service_config_template", {
        "template_id": runtime_id, "template_name": "Runtime", "enabled": True,
        "created_at": now, "updated_at": now,
    })
    row = await AgentDefinitionService(manager_api.handler).create(config_id, runtime_id, "Agent")
    return "/api/v1/agent-definitions/" + row["agent_id"], row, path


async def _grant_runtime(manager_api, runtime_id, gateway_id="gateway-1", **changes):
    now = utc_now()
    return await manager_api.handler.create("instance_service_resource", {
        "jiuwenclaw_id": gateway_id, "resource_id": new_uuid4(), "resource_name": "Runtime",
        "ref_template_id": runtime_id, "enabled": True, "priority": 0,
        "match_expr": [], "created_at": now, "updated_at": now, **changes,
    })


@pytest.mark.asyncio
async def test_binding_uses_agent_id_keeps_config_and_dispatches_secret_privately(
    manager_api, monkeypatch, enabled_registration,
):
    _, simulator = enabled_registration
    _reference_gateway(monkeypatch)
    http = manager_api.http
    original = {"custom_config": [{"client_id": "unrelated-partial"}, {"timeout": 30}], "extra": True}
    path, agent, config_path = await _create_agent(manager_api, data=original)
    await _grant_runtime(manager_api, agent["runtime_template_id"])
    simulator.expected_client_id = agent["agent_id"]
    endpoint = path + "/keycloak-client"
    response = await http.post(endpoint)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["client_id"] == agent["agent_id"]
    assert response.json()["data"]["status"] == "bound"
    assert "generated-secret" not in response.text
    assert simulator.client["attributes"]["jiuwen.agent_id"] == agent["agent_id"]
    assert simulator.queried_client_ids[0] == agent["agent_id"]
    assert (await http.post(endpoint)).status_code == 200
    assert simulator.created == 1 and simulator.probes == 1
    assert (await http.get(config_path)).json()["data"]["data"] == original
    assert (await http.post(config_path + "/keycloak-client")).status_code == 404
    assert "generated-secret" not in (await http.get("/api/v1/agent-definitions/")).text
    payload = manager_api.gateway_sim.calls[-1][3]["data"]
    assert payload["custom_config"] == original["custom_config"]
    assert payload["managed_agent_clients"][agent["agent_id"]]["client_secret"] == "generated-secret"
    assert "_jiuwen_template_operation" not in payload
    updated = await http.patch(config_path, json={"data": {"custom_config": [{"timeout": 60}]}})
    assert updated.status_code == 200
    assert manager_api.gateway_sim.calls[-1][3]["data"]["managed_agent_clients"] == payload["managed_agent_clients"]
    await push.upsert_agent_template_on_gateway(manager_api.handler, "gateway-1", agent["agent_config_template_id"])
    assert manager_api.gateway_sim.calls[-1][3]["data"]["managed_agent_clients"] == payload["managed_agent_clients"]
    assert (await http.delete(endpoint)).json()["data"]["status"] == "unbound"
    assert simulator.deleted == 1
    assert manager_api.gateway_sim.calls[-1][3]["data"]["managed_agent_clients"] == {}
    assert (await http.post(endpoint)).status_code == 200
    assert simulator.created == 2
    assert (await http.delete(path)).status_code == 200
    assert simulator.deleted == 2
    assert (await http.get(endpoint)).status_code == 404


@pytest.mark.asyncio
async def test_custom_json_is_independent_and_registration_failure_is_retryable(
    manager_api, monkeypatch, enabled_registration,
):
    _reference_gateway(monkeypatch)
    _, simulator = enabled_registration
    http = manager_api.http
    original = {"custom_config": [{"client_id": "ordinary", "client_secret": "keep", "timeout": 30}]}
    path, agent, config_path = await _create_agent(manager_api, data=original)
    await _grant_runtime(manager_api, agent["runtime_template_id"])
    simulator.expected_client_id = agent["agent_id"]
    endpoint = path + "/keycloak-client"
    simulator.mapper_failure = True
    assert (await http.post(endpoint)).status_code == 502
    status = (await http.get(endpoint)).json()["data"]
    assert status["status"] == "unbound" and status["operation"] is None and status["error"]
    simulator.mapper_failure = False
    assert (await http.post(endpoint)).status_code == 200
    assert (await http.get(endpoint)).json()["data"]["error"] is None
    assert simulator.client["clientId"] == agent["agent_id"]
    assert (await http.get(config_path)).json()["data"]["data"] == original
    payload = manager_api.gateway_sim.calls[-1][3]["data"]
    assert payload["custom_config"] == original["custom_config"]
    assert payload["managed_agent_clients"][agent["agent_id"]]["client_secret"] == "generated-secret"
    assert (await http.delete(endpoint)).status_code == 200
    assert (await http.get(config_path)).json()["data"]["data"] == original


@pytest.mark.asyncio
async def test_unregistration_failure_preserves_binding_and_delete_retry(manager_api, monkeypatch, enabled_registration):
    service, simulator = enabled_registration
    path, agent, _ = await _create_agent(manager_api)
    simulator.expected_client_id = agent["agent_id"]
    endpoint = path + "/keycloak-client"
    assert (await manager_api.http.post(endpoint)).status_code == 200
    delete = service.delete

    async def fail(*args):
        raise RegistrationError("Keycloak unavailable")

    monkeypatch.setattr(service, "delete", fail)
    assert (await manager_api.http.delete(endpoint)).status_code == 502
    status = (await manager_api.http.get(endpoint)).json()["data"]
    assert status["status"] == "bound" and status["error"]["action"] == "delete"
    assert (await manager_api.http.delete(path)).status_code == 502
    monkeypatch.setattr(service, "delete", delete)
    assert (await manager_api.http.delete(path)).status_code == 200
    assert simulator.deleted == 1


@pytest.mark.asyncio
async def test_slow_gateway_releases_database_transaction_and_serializes_binding(manager_api, monkeypatch, enabled_registration):
    _, simulator = enabled_registration
    _reference_gateway(monkeypatch)
    path, agent, config_path = await _create_agent(manager_api)
    await _grant_runtime(manager_api, agent["runtime_template_id"])
    simulator.expected_client_id = agent["agent_id"]
    entered, release = asyncio.Event(), asyncio.Event()
    original_push = push._update_template_on_gateway

    async def slow(*args):
        entered.set()
        await release.wait()
        return await original_push(*args)

    monkeypatch.setattr(push, "_update_template_on_gateway", slow)
    task = asyncio.create_task(manager_api.http.post(path + "/keycloak-client"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        response = await manager_api.http.get(path + "/keycloak-client")
        assert response.json()["data"]["operation"] == "register"
        assert response.json()["data"]["status"] == "unbound"
        assert (await manager_api.http.post(path + "/keycloak-client")).status_code == 409
        assert (await manager_api.http.delete(path)).status_code == 409
        assert (await manager_api.http.patch(config_path, json={"description": "busy"})).status_code == 409
        # An unrelated SQLite writer is not held behind network I/O.
        await asyncio.wait_for(_create_template(manager_api.http, template_name="Other"), 2)
    finally:
        release.set()
        await task
    assert task.result().status_code == 200


@pytest.mark.asyncio
async def test_gateway_failure_compensates_and_allows_retry(manager_api, monkeypatch, enabled_registration):
    _, simulator = enabled_registration
    _reference_gateway(monkeypatch)
    path, agent, _ = await _create_agent(manager_api)
    await _grant_runtime(manager_api, agent["runtime_template_id"])
    simulator.expected_client_id = agent["agent_id"]
    original_push = push._update_template_on_gateway

    async def fail(*args):
        raise RuntimeError("upstream failed")

    monkeypatch.setattr(push, "_update_template_on_gateway", fail)
    response = await manager_api.http.post(path + "/keycloak-client")
    assert response.status_code == 502
    assert simulator.deleted == 1
    assert (await manager_api.http.get(path + "/keycloak-client")).json()["data"]["status"] == "unbound"
    monkeypatch.setattr(push, "_update_template_on_gateway", original_push)
    assert (await manager_api.http.post(path + "/keycloak-client")).status_code == 200


@pytest.mark.asyncio
async def test_registration_requires_platform_admin(manager_api, monkeypatch):
    async def deny():
        raise HTTPException(status_code=403)
    manager_api.app.dependency_overrides[require_admin] = deny
    response = await manager_api.http.post(f"/api/v1/agent-definitions/{TEMPLATE_ID}/keycloak-client")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_configuration_and_tls_are_reused_off_event_loop(manager_api, monkeypatch, tmp_path):
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", True)
    monkeypatch.setattr(routes.settings, "agent_client_registration_config_file", str(path))
    agent_clients._read_registration_config.cache_clear()
    agent_clients._cached_registration_service.cache_clear()
    main_thread = threading.get_ident()
    reads = []
    contexts = []
    read_text = Path.read_text
    create_context = agent_clients.ssl.create_default_context

    def checked_read_text(file, *args, **kwargs):
        assert threading.get_ident() != main_thread
        reads.append(file)
        return read_text(file, *args, **kwargs)

    def checked_create_context(*args, **kwargs):
        assert threading.get_ident() != main_thread
        context = create_context(*args, **kwargs)
        contexts.append(context)
        return context

    monkeypatch.setattr(Path, "read_text", checked_read_text)
    monkeypatch.setattr(agent_clients.ssl, "create_default_context", checked_create_context)
    for _ in range(2):
        response = await manager_api.http.get(f"{PATH}/capabilities")
        assert response.json() == {"enabled": True, "configured": True}
    assert len(reads) == 1
    assert len(contexts) == 1


def test_configuration_and_ca_changes_invalidate_cached_service(monkeypatch, tmp_path):
    path = tmp_path / "registration.json"
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("CA revision one", encoding="utf-8")
    config = {**CONFIG, "ca_bundle": str(ca_file)}
    path.write_text(json.dumps(config), encoding="utf-8")
    contexts = []

    def create_context(**_kwargs):
        context = object()
        contexts.append(context)
        return context

    monkeypatch.setattr(agent_clients.ssl, "create_default_context", create_context)
    settings = Settings(MANAGER_AGENT_CLIENT_REGISTRATION_ENABLED=True,
                        MANAGER_AGENT_CLIENT_REGISTRATION_CONFIG_FILE=str(path))
    original = agent_clients.load_registration_service(settings)
    assert agent_clients.load_registration_service(settings) is original
    ca_file.write_text("CA revision two with different size", encoding="utf-8")
    rotated = agent_clients.load_registration_service(settings)
    assert rotated is not original
    config["agent_token_audience"] = "new-audience"
    path.write_text(json.dumps(config), encoding="utf-8")
    changed = agent_clients.load_registration_service(settings)
    assert changed is not rotated
    assert changed.config.agent_token_audience == "new-audience"
    assert len(contexts) == 3


@pytest.mark.asyncio
async def test_existing_database_adds_definition_data_without_reset(manager_api):
    from sqlalchemy import text
    from manager_server.models.table_init import _ensure_agent_definition_data_column
    path, agent, _ = await _create_agent(manager_api)
    engine = manager_api.handler.get_engine()
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE agent_definition DROP COLUMN data"))
    await _ensure_agent_definition_data_column(manager_api.handler)
    await _ensure_agent_definition_data_column(manager_api.handler)
    row = await manager_api.handler.get("agent_definition", {"agent_id": agent["agent_id"]})
    assert row.agent_name == agent["agent_name"] and row.data is None


@pytest.mark.asyncio
async def test_disabled_feature_keeps_manual_credentials_and_ordinary_deletion(manager_api, monkeypatch):
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", False)
    original = {"custom_config": [{"client_id": "existing", "client_secret": "existing-secret"}]}
    path, agent, config_path = await _create_agent(manager_api, data=original)
    for method in ("GET", "POST", "DELETE"):
        assert (await manager_api.http.request(method, path + "/keycloak-client")).status_code == 404
    assert (await manager_api.http.delete(path)).status_code == 200
    assert (await manager_api.http.get(config_path)).json()["data"]["data"] == original
    assert (await manager_api.http.delete(config_path)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_bound_agent_requires_cleanup_configuration_before_deletion(
    manager_api, monkeypatch, tmp_path, enabled_registration, enabled,
):
    _, simulator = enabled_registration
    path, agent, _ = await _create_agent(manager_api)
    simulator.expected_client_id = agent["agent_id"]
    assert (await manager_api.http.post(path + "/keycloak-client")).status_code == 200
    monkeypatch.setattr(clients, "load_registration_service", agent_clients.load_registration_service)
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", enabled)
    monkeypatch.setattr(routes.settings, "agent_client_registration_config_file", str(tmp_path / "missing.json"))
    response = await manager_api.http.delete(path)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "bound_client_cleanup_unavailable"
    assert "Enable and configure" in response.json()["detail"]["message"]
    row = await manager_api.handler.get("agent_definition", {"agent_id": agent["agent_id"]})
    assert row.data[clients.BINDING]["client_id"] == agent["agent_id"]
    assert simulator.deleted == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_unbound_agent_deletion_never_loads_keycloak_configuration(manager_api, monkeypatch, tmp_path, enabled):
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", enabled)
    monkeypatch.setattr(routes.settings, "agent_client_registration_config_file", str(tmp_path / "missing.json"))

    def unexpected_load(_settings):
        pytest.fail("Ordinary Agent deletion must not load Keycloak configuration")

    monkeypatch.setattr(clients, "load_registration_service", unexpected_load)
    path, _, _ = await _create_agent(manager_api)
    assert (await manager_api.http.delete(path)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_reservation_release_failure_preserves_original_error(
    manager_api, monkeypatch, enabled_registration, cancelled,
):
    _, agent, _ = await _create_agent(manager_api)
    service = clients.AgentClientService(manager_api.handler)
    keycloak, _ = enabled_registration
    failure = asyncio.CancelledError("cancelled") if cancelled else RegistrationError("original failure")

    async def fail_registration(*_args):
        raise failure

    async def fail_release(_operation):
        raise RuntimeError("database unavailable during release")

    monkeypatch.setattr(service, "_finish", fail_release)
    monkeypatch.setattr(keycloak, "register", fail_registration)
    with pytest.raises(type(failure)) as caught:
        await service.register(agent["agent_id"])
    assert caught.value is failure


@pytest.mark.asyncio
async def test_reservation_release_failure_without_original_error_is_reported(
    manager_api, monkeypatch, enabled_registration,
):
    path, agent, _ = await _create_agent(manager_api)
    _, simulator = enabled_registration
    simulator.expected_client_id = agent["agent_id"]
    assert (await manager_api.http.post(path + "/keycloak-client")).status_code == 200
    service = clients.AgentClientService(manager_api.handler)

    async def fail_release(_operation):
        raise RuntimeError("database unavailable during release")

    monkeypatch.setattr(service, "_finish", fail_release)
    with pytest.raises(RuntimeError, match="database unavailable during release"):
        await service.register(agent["agent_id"])


@pytest.mark.asyncio
async def test_cancelled_registration_compensates_and_preserves_cancellation(
    manager_api, monkeypatch, enabled_registration,
):
    _, simulator = enabled_registration
    _, agent, _ = await _create_agent(manager_api)
    simulator.expected_client_id = agent["agent_id"]
    service = clients.AgentClientService(manager_api.handler)
    entered = asyncio.Event()

    async def block_dispatch(_row, binding, **_kwargs):
        if binding:
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(service, "_dispatch", block_dispatch)
    task = asyncio.create_task(service.register(agent["agent_id"]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    row = await manager_api.handler.get("agent_definition", {"agent_id": agent["agent_id"]})
    assert clients.BINDING not in row.data and clients.OPERATION not in row.data
    assert simulator.deleted == 1


async def _registered_agent_pair(manager_api):
    custom = {"custom_config": [{"client_id": "ordinary", "client_secret": "ordinary-data"}]}
    _, first, config_path = await _create_agent(manager_api, data=custom)
    runtime_id = new_uuid4()
    now = utc_now()
    await manager_api.handler.create("service_config_template", {
        "template_id": runtime_id, "template_name": "Second runtime", "enabled": True,
        "created_at": now, "updated_at": now,
    })
    second = await AgentDefinitionService(manager_api.handler).create(
        first["agent_config_template_id"], runtime_id, "Second Agent",
    )
    for agent, secret in ((first, "secret-a"), (second, "secret-b")):
        await manager_api.handler.update("agent_definition", {"agent_id": agent["agent_id"]}, {
            "data": {clients.BINDING: {"client_id": agent["agent_id"], "client_secret": secret}},
        })
    return first, second, config_path, custom


async def _grant_config(manager_api, config_id, gateway_id):
    now = utc_now()
    await manager_api.handler.create("instance_agent_resource", {
        "jiuwenclaw_id": gateway_id, "resource_id": new_uuid4(), "resource_name": "Agent",
        "ref_template_id": config_id, "enabled": True, "match_expr": [],
        "created_at": now, "updated_at": now,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["bulk", "upsert", "patch"])
async def test_credential_dispatch_is_scoped_to_each_gateway(manager_api, monkeypatch, mode):
    first, second, config_path, custom = await _registered_agent_pair(manager_api)
    config_id = first["agent_config_template_id"]
    expected = {"gateway-a": first, "gateway-b": second, "gateway-empty": None, "gateway-shared": first}
    for gateway_id, agent in expected.items():
        await _grant_config(manager_api, config_id, gateway_id)
        if agent:
            await _grant_runtime(manager_api, agent["runtime_template_id"], gateway_id)
    # Disabled and expired grants never widen a Gateway's credential set.
    await _grant_runtime(manager_api, second["runtime_template_id"], "gateway-empty", enabled=False)
    await _grant_runtime(
        manager_api, first["runtime_template_id"], "gateway-empty",
        expires_at=utc_now() - timedelta(seconds=1),
    )

    async def reachable(*_args):
        return set(expected)

    monkeypatch.setattr(push, "_reachable_gateway_jiuwenclaw_ids", reachable)
    manager_api.gateway_sim.calls.clear()
    if mode == "patch":
        await push.update_template_on_referencing_gateways(
            manager_api.handler, push.AGENT_TEMPLATES_KIND, config_id, {"data": custom},
        )
    else:
        for gateway_id in expected:
            if mode == "bulk":
                await push.sync_referenced_templates_to_gateway(manager_api.handler, gateway_id)
            else:
                await push.upsert_agent_template_on_gateway(manager_api.handler, gateway_id, config_id)
    calls = [call for call in manager_api.gateway_sim.calls if "/agent-templates" in call[2]]
    assert {call[0] for call in calls} == set(expected)
    for gateway_id, _, _, payload in calls:
        agent = expected[gateway_id]
        private = payload["data"][clients.CLIENTS]
        assert set(private) == ({agent["agent_id"]} if agent else set())
        assert payload["data"]["custom_config"] == custom["custom_config"]
        if agent:
            assert private[agent["agent_id"]]["runtime_template_id"] == agent["runtime_template_id"]
            assert private[agent["agent_id"]]["client_secret"] == (
                "secret-a" if agent == first else "secret-b"
            )
        text = json.dumps(payload)
        assert agent == first or "secret-a" not in text
        assert agent == second or "secret-b" not in text
    assert (await manager_api.http.get(config_path)).json()["data"]["data"] == custom

    if mode == "patch":
        manager_api.gateway_sim.calls.clear()
        await push.update_template_on_referencing_gateways(
            manager_api.handler, push.AGENT_TEMPLATES_KIND, config_id, {"data": custom},
            agent_client_overrides={first["agent_id"]: None},
        )
        for gateway_id, _, _, payload in manager_api.gateway_sim.calls:
            assert first["agent_id"] not in payload["data"][clients.CLIENTS]
            assert set(payload["data"][clients.CLIENTS]) == (
                {second["agent_id"]} if gateway_id == "gateway-b" else set()
            )


@pytest.mark.asyncio
async def test_runtime_grant_changes_refresh_only_its_gateway(manager_api, monkeypatch):
    from manager_server.core.instance_resource import instance_service_resource_service as resources

    first, second, config_path, custom = await _registered_agent_pair(manager_api)
    config_id = first["agent_config_template_id"]
    await _grant_config(manager_api, config_id, "gateway-a")
    await _grant_config(manager_api, config_id, "gateway-b")
    await _grant_runtime(manager_api, second["runtime_template_id"], "gateway-b")

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(resources, "sync_runtime_config", noop)
    monkeypatch.setattr(resources, "auto_bind_from_match_expr", noop)
    monkeypatch.setattr(resources, "record_service_template_ref_on_runtime", noop)
    monkeypatch.setattr(resources, "unrecord_service_template_ref_on_runtime", noop)
    service = resources.InstanceServiceResourceService(manager_api.handler)

    def assert_dispatch(agent):
        gateway_id, _, _, payload = manager_api.gateway_sim.calls[-1]
        assert gateway_id == "gateway-a"
        assert set(payload["data"][clients.CLIENTS]) == ({agent["agent_id"]} if agent else set())
        assert payload["data"]["custom_config"] == custom["custom_config"]

    # Registration can precede publication of its runtime grant.
    await push.upsert_agent_template_on_gateway(manager_api.handler, "gateway-a", config_id)
    assert_dispatch(None)
    created = await service.create_resource(
        "gateway-a", first["runtime_template_id"], [[]], resource_name="Runtime", priority=0,
    )
    resource_id = created[0]["resource_id"]
    assert_dispatch(first)
    await service.update_resource("gateway-a", resource_id, [[]], enabled=False)
    assert_dispatch(None)
    await service.update_resource("gateway-a", resource_id, [[]], enabled=True)
    assert_dispatch(first)
    await service.update_resource(
        "gateway-a", resource_id, [[]], ref_template_id=second["runtime_template_id"],
    )
    assert_dispatch(second)
    send = push._create_template_on_gateway

    async def fail(*_args, **_kwargs):
        raise RuntimeError("Gateway unavailable")

    monkeypatch.setattr(push, "_create_template_on_gateway", fail)
    with pytest.raises(ValueError, match="refresh Agent credentials"):
        await service.remove_resource("gateway-a", resource_id)
    assert await manager_api.handler.get("instance_service_resource", {"resource_id": resource_id}) is not None
    monkeypatch.setattr(push, "_create_template_on_gateway", send)
    assert await service.remove_resource("gateway-a", resource_id)
    assert_dispatch(None)
    await service.create_resource(
        "gateway-a", first["runtime_template_id"], [[]], resource_name="Runtime", priority=0,
    )
    assert_dispatch(first)
    assert await service.remove_from_instance("gateway-a", first["runtime_template_id"])
    assert_dispatch(None)
    assert all(call[0] == "gateway-a" for call in manager_api.gateway_sim.calls)
    assert (await manager_api.http.get(config_path)).json()["data"]["data"] == custom
