"""Keycloak registration protocol, permissions and template credential delivery."""

import asyncio
import json
import threading
from pathlib import Path
from urllib.parse import parse_qs
from weakref import WeakValueDictionary

import httpx
import pytest
from fastapi import HTTPException

from manager_server.core.template import agent_template, push_template_to_gateway as push
from manager_server.infrastructure.config import Settings
from manager_server.schemas.template_schemas import AGENT_TEMPLATE_OPERATION_KEY
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
                assert request.url.params["clientId"] == CLIENT_ID
                if self.created and self.lookup_failure == "http":
                    return httpx.Response(503)
                if self.created and self.lookup_failure == "invalid_json":
                    return httpx.Response(200, text="not-json")
                if self.created and self.lookup_failure == "not_found":
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json=[self.client] if self.client else [])
            self.created += 1
            body = json.loads(request.content)
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
    monkeypatch.setattr(agent_template, "load_registration_service", lambda _: service)
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


@pytest.mark.asyncio
async def test_registration_save_preserves_json_and_protects_bound_client(
    manager_api, monkeypatch, enabled_registration,
):
    _, simulator = enabled_registration
    _reference_gateway(monkeypatch)
    http = manager_api.http
    entries = [{"client_id": "manual-id"}, {"timeout": 30}]
    original_data = {"custom_config": entries, "extra": {"preserve": True}}
    path, template_id = await _create_template(http, data=original_data)
    responses = await asyncio.gather(*(
        http.post(f"{path}/keycloak-client", json={"client_id": CLIENT_ID}) for _ in range(2)
    ))
    for response in responses:
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["data"] == {"client_id": CLIENT_ID, "status": "bound"}
    assert simulator.created == 1 and simulator.probes == 1
    credentials = {"client_id": CLIENT_ID, "client_secret": "generated-secret"}
    expected_data = {**original_data, "custom_config": [credentials, *entries]}
    assert manager_api.gateway_sim.calls == [("gateway-1", "PATCH", path, {"data": expected_data})]

    await push.upsert_agent_template_on_gateway(manager_api.handler, "gateway-1", template_id)
    _, method, gateway_path, payload = manager_api.gateway_sim.calls[-1]
    assert method == "POST" and gateway_path == "/api/v1/agent-templates"
    assert payload["data"] == expected_data and "keycloak_client" not in payload

    response = await http.get(path)
    assert "generated-secret" not in response.text
    assert response.json()["data"]["data"] == original_data
    updated = await http.patch(path, json={"data": {"custom_config": [{"timeout": 60}]}})
    assert updated.status_code == 200
    assert updated.json()["data"]["keycloak_client"]["client_id"] == CLIENT_ID
    assert updated.json()["data"]["data"] == {"custom_config": [{"timeout": 60}]}
    assert manager_api.gateway_sim.calls[-1][3]["data"]["custom_config"] == [credentials, {"timeout": 60}]
    for key in ("keycloak_client", AGENT_TEMPLATE_OPERATION_KEY):
        rejected = await http.patch(path, json={"data": {key: {}}})
        assert rejected.status_code == 422
    assert (await http.delete(path)).status_code == 200
    assert simulator.deleted == 1 and (await http.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_registration_conflicts_report_the_reason_and_preserve_manual_credentials(
    manager_api, enabled_registration,
):
    _, simulator = enabled_registration
    http = manager_api.http
    entries = [{"client_id": "manual-client", "client_secret": "manual-secret"}]
    path, _ = await _create_template(http, data={"custom_config": entries})
    body = {"client_id": CLIENT_ID}

    rejected = await http.post(f"{path}/keycloak-client", json=body)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "manual_credentials_present"
    assert "save" in rejected.json()["detail"]["message"]
    assert (await http.get(path)).json()["data"]["data"]["custom_config"] == entries
    assert simulator.created == 0

    assert (await http.patch(path, json={"data": {"custom_config": []}})).status_code == 200
    assert (await http.post(f"{path}/keycloak-client", json=body)).status_code == 200
    rejected = await http.post(f"{path}/keycloak-client", json={"client_id": "other-client"})
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "template_already_bound"

    other, _ = await _create_template(http)
    rejected = await http.post(f"{other}/keycloak-client", json=body)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "client_id_in_use"
    assert simulator.created == 1 and simulator.deleted == 0


@pytest.mark.asyncio
async def test_slow_gateway_does_not_hold_sqlite_write_lock_or_allow_same_template_changes(
    manager_api, monkeypatch, enabled_registration,
):
    _reference_gateway(monkeypatch)
    gateway_waiting, gateway_resume = asyncio.Event(), asyncio.Event()

    async def slow_gateway(*args, **kwargs):
        gateway_waiting.set()
        await gateway_resume.wait()
        return await manager_api.gateway_sim.gateway_request(*args, **kwargs)

    monkeypatch.setattr(push, "gateway_request", slow_gateway)
    http = manager_api.http
    path, _ = await _create_template(http)
    registration = asyncio.create_task(http.post(f"{path}/keycloak-client", json={"client_id": CLIENT_ID}))
    try:
        await asyncio.wait_for(gateway_waiting.wait(), timeout=2)
        # The other caller has a separate process-local lock registry.
        monkeypatch.setattr(agent_template, "_template_mutation_locks", WeakValueDictionary())
        assert (await asyncio.wait_for(_create_template(http), timeout=2))[0] != path
        current = await http.get(path)
        assert current.json()["data"]["data"] is None and AGENT_TEMPLATE_OPERATION_KEY not in current.text
        for method, url, kwargs in (
            ("PATCH", path, {"json": {"template_name": "Concurrent"}}),
            ("DELETE", path, {}),
            ("POST", f"{path}/keycloak-client", {"json": {"client_id": CLIENT_ID}}),
        ):
            rejected = await http.request(method, url, **kwargs)
            assert rejected.status_code == 409
            assert rejected.json()["detail"]["code"] == "template_busy"
    finally:
        gateway_resume.set()
        result = await asyncio.wait_for(registration, timeout=5)
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_gateway_failure_rolls_back_registration_and_allows_retry(
    manager_api, monkeypatch, enabled_registration,
):
    _, simulator = enabled_registration
    _reference_gateway(monkeypatch)
    original = manager_api.gateway_sim.gateway_request
    http = manager_api.http
    path, template_id = await _create_template(http, data={"custom_config": [{"timeout": 30}]})

    async def fail_distribution(jid, method, url, payload, **kwargs):
        if payload["data"]["custom_config"][0].get("client_secret"):
            raise ValueError("Gateway rejected credential value generated-secret")
        return await original(jid, method, url, payload, **kwargs)

    monkeypatch.setattr(push, "gateway_request", fail_distribution)
    response = await http.post(f"{path}/keycloak-client", json={"client_id": CLIENT_ID})
    assert response.status_code == 502 and "generated-secret" not in response.text
    assert simulator.deleted == 1 and simulator.client is None
    assert manager_api.gateway_sim.calls[-1][3] == {"data": {"custom_config": [{"timeout": 30}]}}
    row = await manager_api.handler.get("agent_template", {"template_id": template_id})
    assert row.data == {"custom_config": [{"timeout": 30}]}

    monkeypatch.setattr(push, "gateway_request", original)
    assert (await http.post(f"{path}/keycloak-client", json={"client_id": CLIENT_ID})).status_code == 200


@pytest.mark.asyncio
async def test_registration_requires_platform_admin(manager_api, monkeypatch):
    def deny():
        raise HTTPException(status_code=403, detail="admin required")
    manager_api.app.dependency_overrides[require_admin] = deny
    response = await manager_api.http.post(
        f"/api/v1/agent-templates/{TEMPLATE_ID}/keycloak-client", json={"client_id": CLIENT_ID},
    )
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
