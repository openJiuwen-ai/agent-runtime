"""Keycloak registration protocol, permissions and template credential delivery."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

from manager_server.infrastructure.config import Settings
from manager_server.integrations.keycloak.agent_clients import (
    CLIENT_PREFIX, KeycloakAgentClients, RegistrationConfig, RegistrationError, load_registration_config,
)
from manager_server.integrations.keycloak import agent_clients
from manager_server.routers import agent_client_registration_routers as routes
from manager_server.routers.auth_guards import require_admin

pytestmark = pytest.mark.unit
REGISTRATION_ID = UUID("8bbff4f1-dfa3-48d6-84e0-8dd3368c01f1")
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
                assert request.url.params["clientId"] == CLIENT_PREFIX + REGISTRATION_ID.hex
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
    first = await service.register(REGISTRATION_ID, "My Agent", "admin")
    second = await service.register(REGISTRATION_ID, "Renamed Agent", "admin")
    assert first == second
    assert first["client_secret"] == "generated-secret"
    assert simulator.created == 1 and len(simulator.mappers) == 1 and simulator.probes == 2
    assert simulator.mappers[0]["config"]["included.custom.audience"] == "tools-gateway"


@pytest.mark.asyncio
async def test_failed_registration_rolls_back_owned_client_without_exposing_body(keycloak_service):
    service, simulator = keycloak_service
    simulator.mapper_failure = True
    with pytest.raises(RegistrationError, match="配置 Agent 受众失败：Keycloak 返回 HTTP 403") as error:
        await service.register(REGISTRATION_ID, "My Agent", "admin")
    assert "secret-must-not-leak" not in str(error.value)
    assert simulator.client is None and simulator.deleted == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "invalid_json", "not_found"])
async def test_lookup_failure_after_creation_rolls_back_using_location(keycloak_service, failure):
    service, simulator = keycloak_service
    simulator.lookup_failure = failure
    with pytest.raises(RegistrationError):
        await service.register(REGISTRATION_ID, "My Agent", "admin")
    assert simulator.created == 1
    assert simulator.deleted == 1
    assert simulator.client is None


@pytest.mark.asyncio
async def test_cancel_checks_references_beyond_first_page(manager_api, monkeypatch, keycloak_service):
    from unittest.mock import AsyncMock

    service, simulator = keycloak_service
    await service.register(REGISTRATION_ID, "My Agent", "admin")

    def registration_service():
        return service

    def admin_user():
        return SimpleNamespace(user_id="admin")

    monkeypatch.setattr(routes, "_service", registration_service)
    manager_api.app.dependency_overrides[require_admin] = admin_user
    unbound = [SimpleNamespace(data={}) for _ in range(500)]
    bound = SimpleNamespace(data={"custom_config": [{"client_id": simulator.client["clientId"]}]})
    scan = AsyncMock(side_effect=[unbound, [bound]])
    monkeypatch.setattr(manager_api.handler, "list_records", scan)
    response = await manager_api.http.delete(f"{PATH}/{REGISTRATION_ID}")
    assert response.status_code == 409
    assert simulator.deleted == 0
    assert [call.kwargs["offset"] for call in scan.await_args_list] == [0, 500]
    assert all(call.kwargs["order_by"] == [("id", False)] for call in scan.await_args_list)


@pytest.mark.asyncio
async def test_overlapping_mapper_creation_accepts_matching_existing_audience(keycloak_service):
    service, simulator = keycloak_service
    simulator.mapper_conflict = True
    result = await service.register(REGISTRATION_ID, "My Agent", "admin")
    assert result["client_secret"] == "generated-secret"
    assert simulator.deleted == 0 and len(simulator.mappers) == 1


@pytest.mark.asyncio
async def test_cannot_adopt_or_delete_another_owners_client(keycloak_service):
    service, simulator = keycloak_service
    await service.register(REGISTRATION_ID, "My Agent", "admin")
    with pytest.raises(RegistrationError, match="不属于本次注册"):
        await service.cancel(REGISTRATION_ID, "another-admin")
    assert simulator.deleted == 0


@pytest.mark.asyncio
async def test_unsaved_client_can_be_cancelled_idempotently(keycloak_service):
    service, simulator = keycloak_service
    await service.register(REGISTRATION_ID, "My Agent", "admin")
    assert await service.cancel(REGISTRATION_ID, "admin") is True
    assert await service.cancel(REGISTRATION_ID, "admin") is False
    assert simulator.deleted == 1


@pytest.mark.asyncio
async def test_feature_off_and_invalid_configuration_do_not_break_manager(manager_api, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", False)
    monkeypatch.setattr(routes.settings, "agent_client_registration_config_file", str(tmp_path / "missing.json"))
    response = await manager_api.http.get(f"{PATH}/capabilities")
    assert response.json() == {"enabled": False, "configured": False}
    response = await manager_api.http.post(
        PATH, json={"registration_id": str(REGISTRATION_ID), "template_name": "Agent"},
    )
    assert response.status_code == 404
    monkeypatch.setattr(routes.settings, "agent_client_registration_enabled", True)
    response = await manager_api.http.get(f"{PATH}/capabilities")
    assert response.json()["configured"] is False
    assert (await manager_api.http.get("/api/v1/agent-templates/")).status_code == 200


@pytest.mark.asyncio
async def test_registration_save_preserves_json_and_protects_bound_client(manager_api, monkeypatch, keycloak_service):
    service, simulator = keycloak_service

    def registration_service():
        return service

    def admin_user():
        return SimpleNamespace(user_id="admin")

    monkeypatch.setattr(routes, "_service", registration_service)
    manager_api.app.dependency_overrides[require_admin] = admin_user
    response = await manager_api.http.post(
        PATH, json={"registration_id": str(REGISTRATION_ID), "template_name": "Agent"},
    )
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    credentials = response.json()
    entries = [
        {"keep": {"nested": [1, 2]}},
        {"client_id": credentials["client_id"], "client_secret": credentials["client_secret"]},
    ]
    saved = await manager_api.http.post(
        "/api/v1/agent-templates/", json={"template_name": "Agent", "data": {"custom_config": entries}},
    )
    assert saved.status_code == 200
    template_id = saved.json()["data"]["template_id"]
    result = await manager_api.http.get(f"/api/v1/agent-templates/{template_id}")
    assert result.json()["data"]["data"]["custom_config"] == entries
    instance_id = await manager_api.create_instance()
    await manager_api.handler.update("instance_info", {"jiuwenclaw_id": instance_id}, {"gateway_status": "online"})
    published = await manager_api.http.post(f"/api/v1/instances/{instance_id}/agent-resources", json={
        "ref_template_id": template_id, "resource_name": "registered-agent", "match_exprs": [[]],
    })
    assert published.status_code == 200
    deliveries = [body for _, method, path, body in manager_api.gateway_sim.calls
                  if method == "POST" and path == "/api/v1/agent-templates"]
    assert deliveries[-1]["data"]["custom_config"] == entries
    cancelled = await manager_api.http.delete(f"{PATH}/{REGISTRATION_ID}")
    assert cancelled.status_code == 409 and simulator.deleted == 0


@pytest.mark.asyncio
async def test_registration_requires_platform_admin(manager_api, monkeypatch):
    def deny():
        raise HTTPException(status_code=403, detail="admin required")
    manager_api.app.dependency_overrides[require_admin] = deny
    response = await manager_api.http.post(
        PATH, json={"registration_id": str(REGISTRATION_ID), "template_name": "Agent"},
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
