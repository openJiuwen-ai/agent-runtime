"""A2A Manager catalog/policy M1 API coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from conftest import ManagerApiHarness

from manager_server.core.template.a2a_discovery import (
    A2ADiscoveryError,
    DiscoveredCard,
    _normalize_url,
    _pinned_request,
    _select_interface,
    _validate_target,
    critical_identity,
)
from manager_server.core.template.push_agent_template_to_gateway import (
    sync_agent_resource_to_gateway,
)
from manager_server.core.template.push_template_to_gateway import (
    rebuild_jid_template_ref_for_gateway,
    sync_referenced_templates_to_gateway,
)
from manager_server.infrastructure.utils import utc_now

pytestmark = pytest.mark.unit


def test_a2a_discovery_requires_protocol_version_for_selected_interface():
    with pytest.raises(A2ADiscoveryError) as exc_info:
        _select_interface(
            {
                "name": "Legacy Agent",
                "url": "https://agents.example.com/a2a",
                "preferredTransport": "JSONRPC",
            }
        )

    assert exc_info.value.code == "CARD_INVALID"


def test_a2a_discovery_skips_versionless_interface_candidate():
    selected = _select_interface(
        {
            "name": "Compatible Agent",
            "supportedInterfaces": [
                {
                    "url": "https://agents.example.com/legacy",
                    "protocolBinding": "JSONRPC",
                },
                {
                    "url": "https://agents.example.com/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                },
            ],
        }
    )

    assert selected == {
        "url": "https://agents.example.com/a2a",
        "protocol_binding": "JSONRPC",
        "protocol_version": "1.0",
    }


def _agent_body(*, credential: str = "secret-token") -> dict:
    return {
        "discovery_id": "replaced-by-test-helper",
        "template_name": "Weather Agent",
        "description": "Managed outbound agent",
        "a2a_tags": ["weather"],
        "credential": credential,
        "connect_timeout_seconds": 5,
        "sync_wait_seconds": 30,
    }


@pytest.fixture(autouse=True)
def fake_discovery(monkeypatch: pytest.MonkeyPatch):
    async def _fetch(url: str, card_path: str | None = None, **_kwargs) -> DiscoveredCard:
        return DiscoveredCard(
            source_url=url,
            card_path=card_path or "/.well-known/agent-card.json",
            card_url=f"{url.rstrip('/')}{card_path or '/.well-known/agent-card.json'}",
            card_fingerprint="sha256:test",
            agent_card={
                "name": "Weather Agent",
                "version": "1.0",
                "securityRequirements": [],
            },
            selected_interface={
                "protocol_binding": "JSONRPC",
                "protocol_version": "1.0",
                "url": "https://agents.example.com/a2a",
            },
        )

    monkeypatch.setattr("manager_server.core.template.a2a_discovery.fetch_agent_card", _fetch)


async def _create_agent(
    h: ManagerApiHarness,
    *,
    credential: str = "secret-token",
    url: str = "https://agents.example.com",
) -> dict:
    discovery = await h.post_json("/a2a-outbound-discoveries", {"url": url})
    body = _agent_body(credential=credential)
    body["discovery_id"] = discovery["discovery_id"]
    return await h.post_json("/a2a-outbound-templates", body)


@pytest.mark.asyncio
async def test_a2a_agent_crud_masks_and_explicitly_edits_credential(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    created = await _create_agent(h)
    template_id = created["template_id"]
    assert created["credential_configured"] is True
    assert "credential" not in created

    detail = await h.get_json(f"/a2a-outbound-templates/{template_id}")
    assert "credential" not in detail
    edit = await h.get_json(f"/a2a-outbound-templates/{template_id}/edit")
    assert edit["credential"] == "secret-token"

    kept = await h.patch_json(f"/a2a-outbound-templates/{template_id}", {"credential": ""})
    assert kept["credential_configured"] is True
    edit = await h.get_json(f"/a2a-outbound-templates/{template_id}/edit")
    assert edit["credential"] == "secret-token"

    replaced = await h.patch_json(
        f"/a2a-outbound-templates/{template_id}", {"credential": "replacement"}
    )
    assert replaced["credential_configured"] is True
    edit = await h.get_json(f"/a2a-outbound-templates/{template_id}/edit")
    assert edit["credential"] == "replacement"

    cleared = await h.patch_json(
        f"/a2a-outbound-templates/{template_id}", {"clear_credential": True}
    )
    assert cleared["credential_configured"] is False
    edit = await h.get_json(f"/a2a-outbound-templates/{template_id}/edit")
    assert edit["credential"] is None

    await h.delete_ok(f"/a2a-outbound-templates/{template_id}")


@pytest.mark.asyncio
async def test_a2a_agent_rejects_card_mutation_and_unsafe_card_path(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    for card_path in (
        "https://evil.example/card",
        "//evil.example/card",
        "../secret",
        "/cards/../secret",
        "/cards/%2e%2e/secret",
        "/card?target=other",
    ):
        response = await h.http.post(
            h.templates_url("/a2a-outbound-discoveries"),
            json={"url": "https://agents.example.com", "card_path": card_path},
        )
        assert response.status_code == 422

    created = await _create_agent(h)
    response = await h.http.patch(
        h.templates_url(f"/a2a-outbound-templates/{created['template_id']}"),
        json={"agent_card": {"name": "forged"}},
    )
    assert response.status_code == 422
    detail = await h.get_json(f"/a2a-outbound-templates/{created['template_id']}")
    assert detail["card_revision"] == 1
    assert detail["agent_card"]["name"] == "Weather Agent"


@pytest.mark.asyncio
async def test_a2a_agent_rejects_conflicting_credential_operations(
    manager_api: ManagerApiHarness,
):
    created = await _create_agent(manager_api)
    response = await manager_api.http.patch(
        manager_api.templates_url(f"/a2a-outbound-templates/{created['template_id']}"),
        json={"credential": "replacement", "clear_credential": True},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a2a_policy_validates_members_revisions_and_blocks_agent_delete(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    agent = await _create_agent(h)
    template_id = agent["template_id"]

    policy = await h.post_json(
        "/a2a-access-policies",
        {
            "policy_name": "Production allowlist",
            "mode": "allowlist",
            "member_template_ids": [template_id, template_id],
        },
    )
    policy_id = policy["policy_id"]
    assert policy["member_template_ids"] == [template_id]
    assert policy["revision"] == 1

    updated = await h.patch_json(f"/a2a-access-policies/{policy_id}", {"mode": "denylist"})
    assert updated["mode"] == "denylist"
    assert updated["revision"] == 2

    blocked = await h.http.delete(h.templates_url(f"/a2a-outbound-templates/{template_id}"))
    assert blocked.status_code == 400

    await h.delete_ok(f"/a2a-access-policies/{policy_id}")
    await h.delete_ok(f"/a2a-outbound-templates/{template_id}")


@pytest.mark.asyncio
async def test_a2a_policy_rejects_unknown_member(manager_api: ManagerApiHarness):
    response = await manager_api.http.post(
        manager_api.templates_url("/a2a-access-policies"),
        json={
            "policy_name": "Broken policy",
            "mode": "allowlist",
            "member_template_ids": ["missing-template"],
        },
    )
    assert response.status_code == 400
    assert "missing-template" in response.text


@pytest.mark.asyncio
async def test_a2a_policy_update_rejects_unknown_member_and_delete_blocks_agent_ref(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    policy = await h.post_json(
        "/a2a-access-policies",
        {"policy_name": "Referenced policy", "mode": "denylist"},
    )
    policy_id = policy["policy_id"]

    response = await h.http.patch(
        h.templates_url(f"/a2a-access-policies/{policy_id}"),
        json={"member_template_ids": ["missing-template"]},
    )
    assert response.status_code == 400

    response = await h.http.post(
        h.templates_url("/agent-templates/"),
        json={
            "template_name": "Agent using A2A policy",
            "template_ref": {"a2a_access_policy": [policy_id]},
        },
    )
    assert response.status_code == 200

    blocked = await h.http.delete(h.templates_url(f"/a2a-access-policies/{policy_id}"))
    assert blocked.status_code == 400
    assert "referenced by an agent template" in blocked.text


@pytest.mark.asyncio
async def test_a2a_policy_empty_modes_disabled_state_and_reference_count(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    allowlist = await h.post_json(
        "/a2a-access-policies",
        {"policy_name": "Deny all", "mode": "allowlist", "member_template_ids": []},
    )
    denylist = await h.post_json(
        "/a2a-access-policies",
        {
            "policy_name": "Disabled policy",
            "mode": "denylist",
            "member_template_ids": [],
            "enabled": False,
        },
    )
    assert allowlist["member_template_ids"] == []
    assert denylist["member_template_ids"] == []
    assert denylist["enabled"] is False

    agent_response = await h.http.post(
        h.templates_url("/agent-templates/"),
        json={
            "template_name": "Agent using disabled policy",
            "template_ref": {"a2a_access_policy": [denylist["policy_id"]]},
        },
    )
    assert agent_response.status_code == 200
    agent = agent_response.json()["data"]
    detail = await h.get_json(f"/a2a-access-policies/{denylist['policy_id']}")
    assert detail["reference_count"] == 1
    listed = await h.get_json("/a2a-access-policies")
    listed_policy = next(
        item for item in listed["items"] if item["policy_id"] == denylist["policy_id"]
    )
    assert listed_policy["reference_count"] == 1

    agent_delete = await h.http.delete(h.templates_url(f"/agent-templates/{agent['template_id']}"))
    assert agent_delete.status_code == 200
    unreferenced = await h.get_json(f"/a2a-access-policies/{denylist['policy_id']}")
    assert unreferenced["reference_count"] == 0
    await h.delete_ok(f"/a2a-access-policies/{allowlist['policy_id']}")
    await h.delete_ok(f"/a2a-access-policies/{denylist['policy_id']}")


@pytest.mark.asyncio
async def test_agent_template_rejects_invalid_a2a_policy_references(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    unknown = await h.http.post(
        h.templates_url("/agent-templates/"),
        json={
            "template_name": "Unknown policy",
            "template_ref": {"a2a_access_policy": ["missing-policy"]},
        },
    )
    assert unknown.status_code == 400

    multiple = await h.http.post(
        h.templates_url("/agent-templates/"),
        json={
            "template_name": "Multiple policies",
            "template_ref": {"a2a_access_policy": ["one", "two"]},
        },
    )
    assert multiple.status_code == 422

    policy = await h.post_json(
        "/a2a-access-policies", {"policy_name": "Valid policy", "mode": "allowlist"}
    )
    expression = await h.http.post(
        h.templates_url("/agent-templates/"),
        json={
            "template_name": "Expression policy",
            "template_ref": {"a2a_access_policy": [f"{policy['policy_id']} or ${{user::alice}}"]},
        },
    )
    assert expression.status_code == 400

    agent_response = await h.http.post(
        h.templates_url("/agent-templates/"), json={"template_name": "Update target"}
    )
    assert agent_response.status_code == 200
    agent = agent_response.json()["data"]
    update = await h.http.patch(
        h.templates_url(f"/agent-templates/{agent['template_id']}"),
        json={"template_ref": {"a2a_access_policy": ["missing-policy"]}},
    )
    assert update.status_code == 400


@pytest.mark.asyncio
async def test_a2a_policy_slot_is_indexed_for_gateway_projection(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch
):
    async def reachable(_handler):
        return ["gateway-1"]

    monkeypatch.setattr(
        "manager_server.core.template.push_template_to_gateway.list_reachable_jiuwenclaw_ids",
        reachable,
    )
    outbound = await _create_agent(manager_api)
    policy = await manager_api.post_json(
        "/a2a-access-policies",
        {
            "policy_name": "M4 policy",
            "mode": "allowlist",
            "member_template_ids": [outbound["template_id"]],
        },
    )
    agent_response = await manager_api.http.post(
        manager_api.templates_url("/agent-templates/"),
        json={
            "template_name": "Bound agent",
            "template_ref": {"a2a_access_policy": [policy["policy_id"]]},
        },
    )
    agent = agent_response.json()["data"]
    now = utc_now()
    await manager_api.handler.create(
        "instance_agent_resource",
        {
            "jiuwenclaw_id": "gateway-1",
            "resource_id": "resource-1",
            "resource_name": "Bound agent",
            "ref_template_id": agent["template_id"],
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
    )
    await rebuild_jid_template_ref_for_gateway(manager_api.handler, "gateway-1")
    rows = await manager_api.handler.list_records(
        "jid_template_ref", {"jiuwenclaw_id": "gateway-1"}, limit=10, offset=0
    )
    assert {(row.slot, row.template_id) for row in rows} == {
        ("a2a_access_policy", policy["policy_id"]),
        ("a2a_outbound_projection", outbound["template_id"]),
    }

    manager_api.gateway_sim.calls.clear()
    await sync_referenced_templates_to_gateway(manager_api.handler, "gateway-1")
    creates = [call for call in manager_api.gateway_sim.calls if call[1] == "POST"]
    paths = [call[2] for call in creates]
    assert (
        paths.index("/api/v1/a2a-outbound-templates")
        < paths.index("/api/v1/a2a-access-policies")
        < paths.index("/api/v1/agent-templates")
    )
    outbound_payload = next(
        call[3] for call in creates if call[2] == "/api/v1/a2a-outbound-templates"
    )
    assert outbound_payload["credential"] == {
        "operation": "replace",
        "value": "secret-token",
    }
    assert "pending_revision" not in outbound_payload
    assert "last_error_code" not in outbound_payload
    defaults = {
        "allow_http": False, "allow_loopback": False,
        "allow_private_network": False, "allow_public_http": False,
    }
    assert outbound_payload["data"]["network_policy"] == defaults
    for settings in [{**defaults, "allow_http": True, "allow_private_network": True}, defaults]:
        manager_api.gateway_sim.calls.clear()
        response = await manager_api.http.put(
            manager_api.templates_url("/a2a-discovery-settings"), json=settings
        )
        assert response.status_code == 200
        patches = [call for call in manager_api.gateway_sim.calls
                   if call[1] == "PATCH" and "/a2a-outbound-templates/" in call[2]]
        assert patches
        assert all(call[3]["data"]["network_policy"] == settings for call in patches)
        assert all(call[3]["credential"] == {"operation": "keep"}
                   for call in patches)
        manager_api.gateway_sim.calls.clear()
        await sync_referenced_templates_to_gateway(manager_api.handler, "gateway-1")
        synced = next(call[3] for call in manager_api.gateway_sim.calls
                      if call[1] == "POST" and call[2] == "/api/v1/a2a-outbound-templates")
        assert synced["data"]["network_policy"] == settings

    from manager_server.core.template import push_template_to_gateway as push

    async def two_gateways(*args):
        return {"gateway-1", "gateway-2"}

    monkeypatch.setattr(push, "_referencing_reachable_jids", two_gateways)
    monkeypatch.setattr(push, "collect_referenced_jiuwenclaw_ids_for_template", two_gateways)
    original_request = push.gateway_request
    attempted = []

    async def fail_first_gateway(jid, method, path, payload, **kwargs):
        attempted.append(jid)
        if jid == "gateway-1":
            raise TimeoutError("test gateway timeout")
        assert payload["credential"] == {"operation": "keep"}
        assert payload["source_url"] == outbound["source_url"]
        return await original_request(jid, method, path, payload, **kwargs)

    monkeypatch.setattr(push, "gateway_request", fail_first_gateway)
    response = await manager_api.http.put(
        manager_api.templates_url("/a2a-discovery-settings"), json=defaults
    )
    assert response.status_code == 502
    assert "saved" in response.json()["detail"]
    assert attempted == ["gateway-1", "gateway-2"]
    monkeypatch.setattr(push, "gateway_request", original_request)
    response = await manager_api.http.put(
        manager_api.templates_url("/a2a-discovery-settings"), json=defaults
    )
    assert response.status_code == 200

    manager_api.gateway_sim.calls.clear()
    await manager_api.patch_json(
        f"/a2a-outbound-templates/{outbound['template_id']}",
        {"template_name": "Renamed outbound"},
    )
    outbound_patch = next(
        call
        for call in manager_api.gateway_sim.calls
        if call[1] == "PATCH" and call[2].startswith("/api/v1/a2a-outbound-templates/")
    )
    assert outbound_patch[3]["credential"] == {"operation": "keep"}

    manager_api.gateway_sim.calls.clear()
    await manager_api.patch_json(
        f"/a2a-access-policies/{policy['policy_id']}", {"mode": "denylist"}
    )
    policy_patch = next(
        call
        for call in manager_api.gateway_sim.calls
        if call[1] == "PATCH" and call[2].startswith("/api/v1/a2a-access-policies/")
    )
    assert {
        "policy_name",
        "description",
        "mode",
        "member_template_ids",
        "enabled",
        "revision",
        "updated_at",
    } <= policy_patch[3].keys()
    assert any(
        call[1] == "DELETE"
        and call[2] == f"/api/v1/a2a-outbound-templates/{outbound['template_id']}"
        for call in manager_api.gateway_sim.calls
    )


@pytest.mark.asyncio
async def test_empty_denylist_fans_out_new_agent_to_referencing_gateway(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch
):
    async def reachable(_handler):
        return ["gateway-1"]

    monkeypatch.setattr(
        "manager_server.core.template.push_template_to_gateway.list_reachable_jiuwenclaw_ids",
        reachable,
    )
    policy = await manager_api.post_json(
        "/a2a-access-policies",
        {"policy_name": "All registered", "mode": "denylist", "member_template_ids": []},
    )
    agent_response = await manager_api.http.post(
        manager_api.templates_url("/agent-templates/"),
        json={
            "template_name": "Bound agent",
            "template_ref": {"a2a_access_policy": [policy["policy_id"]]},
        },
    )
    agent = agent_response.json()["data"]
    now = utc_now()
    await manager_api.handler.create(
        "instance_agent_resource",
        {
            "jiuwenclaw_id": "gateway-1",
            "resource_id": "resource-fanout",
            "resource_name": "Bound agent",
            "ref_template_id": agent["template_id"],
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
    )
    await rebuild_jid_template_ref_for_gateway(manager_api.handler, "gateway-1")

    manager_api.gateway_sim.calls.clear()
    outbound = await _create_agent(manager_api, credential="", url="https://new-agent.example.com")
    assert any(
        call[0] == "gateway-1"
        and call[1] == "POST"
        and call[2] == "/api/v1/a2a-outbound-templates"
        and call[3]["template_id"] == outbound["template_id"]
        for call in manager_api.gateway_sim.calls
    )


@pytest.mark.asyncio
async def test_instance_binding_pushes_a2a_dependencies_before_agent(
    manager_api: ManagerApiHarness,
):
    outbound = await _create_agent(manager_api, credential="")
    policy = await manager_api.post_json(
        "/a2a-access-policies",
        {
            "policy_name": "Binding policy",
            "mode": "allowlist",
            "member_template_ids": [outbound["template_id"]],
        },
    )
    agent_response = await manager_api.http.post(
        manager_api.templates_url("/agent-templates/"),
        json={
            "template_name": "Binding target",
            "template_ref": {"a2a_access_policy": [policy["policy_id"]]},
        },
    )
    agent = agent_response.json()["data"]

    manager_api.gateway_sim.calls.clear()
    await sync_agent_resource_to_gateway(
        manager_api.handler,
        "gateway-binding",
        "resource-binding",
        agent["template_id"],
        was_first_for_template=True,
        resource_payload={
            "resource_id": "resource-binding",
            "ref_template_id": agent["template_id"],
            "enabled": True,
        },
    )
    paths = [call[2] for call in manager_api.gateway_sim.calls if call[1] == "POST"]
    assert (
        paths.index("/api/v1/a2a-outbound-templates")
        < paths.index("/api/v1/a2a-access-policies")
        < paths.index("/api/v1/agent-templates")
        < paths.index("/api/v1/instance-agent-resources")
    )


@pytest.mark.asyncio
async def test_a2a_refresh_requires_confirmation_for_critical_change(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch
):
    created = await _create_agent(manager_api)

    async def changed_card(_url: str, _path: str | None = None, **_kwargs) -> DiscoveredCard:
        base = DiscoveredCard(
            source_url="https://agents.example.com",
            card_path="/.well-known/agent-card.json",
            card_url="https://agents.example.com/.well-known/agent-card.json",
            card_fingerprint="sha256:critical-change",
            agent_card={"name": "Weather Agent", "securityRequirements": [{"bearer": []}]},
            selected_interface={
                "protocol_binding": "JSONRPC",
                "protocol_version": "1.0",
                "url": "https://agents.example.com/a2a-v2",
            },
        )
        return base

    monkeypatch.setattr(
        "manager_server.core.template.a2a_outbound_template.fetch_agent_card", changed_card
    )
    refreshed = await manager_api.post_json(
        f"/a2a-outbound-templates/{created['template_id']}:refresh", {}
    )
    assert refreshed["card_revision"] == 1
    assert refreshed["pending_revision"]["selected_interface"]["url"].endswith("a2a-v2")

    accepted = await manager_api.post_json(
        f"/a2a-outbound-templates/{created['template_id']}:confirm-revision",
        {"accept": True},
    )
    assert accepted["card_revision"] == 2
    assert accepted["pending_revision"] is None
    assert accepted["selected_interface"]["url"].endswith("a2a-v2")


@pytest.mark.asyncio
async def test_a2a_refresh_applies_noncritical_card_change(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch
):
    created = await _create_agent(manager_api)

    async def changed_card(_url: str, _path: str | None = None, **_kwargs) -> DiscoveredCard:
        original = DiscoveredCard(
            source_url="https://agents.example.com",
            card_path="/.well-known/agent-card.json",
            card_url="https://agents.example.com/.well-known/agent-card.json",
            card_fingerprint="sha256:test",
            agent_card={"name": "Weather Agent", "version": "1.0", "securityRequirements": []},
            selected_interface={
                "protocol_binding": "JSONRPC",
                "protocol_version": "1.0",
                "url": "https://agents.example.com/a2a",
            },
        )
        return replace(
            original,
            card_fingerprint="sha256:description-change",
            agent_card={**original.agent_card, "description": "new description"},
        )

    monkeypatch.setattr(
        "manager_server.core.template.a2a_outbound_template.fetch_agent_card", changed_card
    )
    refreshed = await manager_api.post_json(
        f"/a2a-outbound-templates/{created['template_id']}:refresh", {}
    )
    assert refreshed["card_revision"] == 2
    assert refreshed["pending_revision"] is None
    assert refreshed["agent_card"]["description"] == "new description"


@pytest.mark.asyncio
async def test_duplicate_registration_does_not_consume_candidate(
    manager_api: ManagerApiHarness,
):
    first = await _create_agent(manager_api)
    discovery = await manager_api.post_json(
        "/a2a-outbound-discoveries", {"url": "https://agents.example.com"}
    )
    body = _agent_body()
    body["discovery_id"] = discovery["discovery_id"]
    duplicate = await manager_api.http.post(
        manager_api.templates_url("/a2a-outbound-templates"), json=body
    )
    assert duplicate.status_code == 400

    await manager_api.delete_ok(f"/a2a-outbound-templates/{first['template_id']}")
    retried = await manager_api.post_json("/a2a-outbound-templates", body)
    assert retried["template_name"] == "Weather Agent"


@pytest.mark.asyncio
async def test_discovery_candidate_is_single_use_and_expires(
    manager_api: ManagerApiHarness,
):
    discovery = await manager_api.post_json(
        "/a2a-outbound-discoveries", {"url": "https://agents.example.com"}
    )
    body = _agent_body()
    body["discovery_id"] = discovery["discovery_id"]
    await manager_api.post_json("/a2a-outbound-templates", body)
    reused = await manager_api.http.post(
        manager_api.templates_url("/a2a-outbound-templates"), json=body
    )
    assert reused.status_code == 400
    assert "not found or expired" in reused.text

    expired = await manager_api.post_json(
        "/a2a-outbound-discoveries", {"url": "https://other.example.com"}
    )
    await manager_api.handler.update(
        "a2a_outbound_discovery",
        {"discovery_id": expired["discovery_id"]},
        {"expires_at": utc_now() - timedelta(seconds=1)},
    )
    expired_body = _agent_body()
    expired_body["discovery_id"] = expired["discovery_id"]
    response = await manager_api.http.post(
        manager_api.templates_url("/a2a-outbound-templates"), json=expired_body
    )
    assert response.status_code == 400
    assert "not found or expired" in response.text


@pytest.mark.asyncio
async def test_refresh_failure_is_non_2xx_and_persists_error(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch
):
    created = await _create_agent(manager_api)

    async def failed(_url: str, _path: str | None = None, **_kwargs) -> DiscoveredCard:
        raise A2ADiscoveryError("CARD_FETCH_FAILED", "Agent Card request failed")

    monkeypatch.setattr(
        "manager_server.core.template.a2a_outbound_template.fetch_agent_card", failed
    )
    response = await manager_api.http.post(
        manager_api.templates_url(f"/a2a-outbound-templates/{created['template_id']}:refresh")
    )
    assert response.status_code == 400
    detail = await manager_api.get_json(f"/a2a-outbound-templates/{created['template_id']}")
    assert detail["last_error_code"] == "CARD_FETCH_FAILED"


@pytest.mark.asyncio
async def test_reject_pending_revision_keeps_confirmed_card(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch
):
    created = await _create_agent(manager_api)

    async def changed(_url: str, _path: str | None = None, **_kwargs) -> DiscoveredCard:
        return DiscoveredCard(
            source_url="https://agents.example.com",
            card_path="/.well-known/agent-card.json",
            card_url="https://agents.example.com/.well-known/agent-card.json",
            card_fingerprint="sha256:pending",
            agent_card={"name": "Weather Agent", "securityRequirements": []},
            selected_interface={
                "protocol_binding": "JSONRPC",
                "protocol_version": "1.0",
                "url": "https://agents.example.com/new-endpoint",
            },
        )

    monkeypatch.setattr(
        "manager_server.core.template.a2a_outbound_template.fetch_agent_card", changed
    )
    await manager_api.post_json(f"/a2a-outbound-templates/{created['template_id']}:refresh", {})
    rejected = await manager_api.post_json(
        f"/a2a-outbound-templates/{created['template_id']}:confirm-revision",
        {"accept": False},
    )
    assert rejected["pending_revision"] is None
    assert rejected["card_revision"] == 1
    assert rejected["selected_interface"]["url"].endswith("/a2a")


def test_discovery_normalizes_direct_card_and_rejects_unsafe_paths():
    source, path, card_url = _normalize_url("https://agents.example.com/custom/card.json", None)
    assert source == "https://agents.example.com/"
    assert path == "/custom/card.json"
    assert card_url == "https://agents.example.com/custom/card.json"
    with pytest.raises(ValueError):
        _normalize_url("https://agents.example.com", "/cards/%2e%2e/secret")


def test_critical_identity_tolerates_dirty_security_schemes():
    identity = critical_identity(
        {"securitySchemes": ["dirty"], "provider": "dirty"},
        {"url": "https://agents.example.com/a2a", "protocol_binding": "JSONRPC"},
    )
    assert identity[3] == []


@pytest.mark.asyncio
async def test_discovery_blocks_private_target_and_pins_validated_address(
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio

    loop = asyncio.get_running_loop()

    async def private_dns(*_args, **_kwargs):
        return [(None, None, None, None, ("169.254.169.254", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", private_dns)
    with pytest.raises(A2ADiscoveryError, match="private network"):
        await _validate_target("https://agents.example.com/card.json")

    async with httpx.AsyncClient(trust_env=False) as client:
        request = _pinned_request(
            client,
            "https://agents.example.com/card.json",
            "agents.example.com",
            "203.0.113.10",
        )
    assert str(request.url) == "https://203.0.113.10/card.json"
    assert request.headers["host"] == "agents.example.com"
    assert request.extensions["sni_hostname"] == "agents.example.com"


@pytest.mark.asyncio
async def test_network_policy_sync_continues_across_pages(manager_api, monkeypatch):
    from manager_server.core.template import a2a_outbound_template as outbound_module
    from manager_server.core.template import push_template_to_gateway as push
    from manager_server.core.template.a2a_discovery_settings import A2ADiscoverySettingsService

    first = await _create_agent(manager_api, url="https://first.example.com")
    row = await manager_api.handler.get("a2a_outbound_template", {"template_id": first["template_id"]})
    values = {key: value for key, value in vars(row).items() if not key.startswith("_")}
    values.pop("id", None)
    values["template_id"] = "second-template"
    values["registration_key"] = "second-registration"
    await manager_api.handler.create("a2a_outbound_template", values)
    monkeypatch.setattr(outbound_module, "_REFERENCE_SCAN_PAGE_SIZE", 1)
    attempted = []

    async def sync(handler, kind, template_id, payload, **kwargs):
        attempted.append(template_id)
        assert payload["credential"] == {"operation": "keep"}
        assert kwargs["continue_on_error"] is True
        assert kwargs["network_policy"]["allow_http"] is False
        if template_id == first["template_id"]:
            raise TimeoutError("first template failed")

    monkeypatch.setattr(push, "update_template_on_referencing_gateways", sync)
    service = A2ADiscoverySettingsService(manager_api.handler)
    with pytest.raises(TimeoutError):
        await service.sync(await service.get())
    assert attempted == [first["template_id"], "second-template"]


@pytest.mark.asyncio
async def test_a2a_discovery_settings_are_persisted(manager_api: ManagerApiHarness):
    url = manager_api.templates_url("/a2a-discovery-settings")
    initial = await manager_api.http.get(url)
    assert initial.status_code == 200
    assert initial.json()["data"] == {
        "allow_http": False,
        "allow_loopback": False,
        "allow_private_network": False,
        "allow_public_http": False,
    }

    updated = await manager_api.http.put(
        url,
        json={
            "allow_http": True,
            "allow_loopback": True,
            "allow_private_network": True,
            "allow_public_http": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["data"] == {
        "allow_http": True,
        "allow_loopback": True,
        "allow_private_network": True,
        "allow_public_http": True,
    }

    persisted = await manager_api.http.get(url)
    assert persisted.json()["data"] == {
        "allow_http": True,
        "allow_loopback": True,
        "allow_private_network": True,
        "allow_public_http": True,
    }


@pytest.mark.asyncio
async def test_discovery_uses_persisted_network_settings(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch
):
    observed: dict[str, bool] = {}

    async def fetch(url: str, card_path: str | None = None, **kwargs) -> DiscoveredCard:
        observed.update(kwargs)
        return DiscoveredCard(
            source_url=url,
            card_path=card_path or "/.well-known/agent-card.json",
            card_url=f"{url.rstrip('/')}/.well-known/agent-card.json",
            card_fingerprint="sha256:settings",
            agent_card={"name": "Local Agent"},
            selected_interface={
                "protocol_binding": "JSONRPC",
                "protocol_version": "1.0",
                "url": "http://127.0.0.1:19110/a2a",
            },
        )

    monkeypatch.setattr("manager_server.core.template.a2a_discovery.fetch_agent_card", fetch)
    await manager_api.http.put(
        manager_api.templates_url("/a2a-discovery-settings"),
        json={
            "allow_http": True,
            "allow_loopback": True,
            "allow_private_network": True,
            "allow_public_http": True,
        },
    )
    response = await manager_api.http.post(
        manager_api.templates_url("/a2a-outbound-discoveries"),
        json={"url": "http://127.0.0.1:19110"},
    )
    assert response.status_code == 200
    assert observed == {
        "allow_http": True,
        "allow_loopback": True,
        "allow_private_network": True,
        "allow_public_http": True,
    }


@pytest.mark.asyncio
async def test_http_loopback_requires_both_discovery_switches(
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio

    loop = asyncio.get_running_loop()

    async def loopback_dns(*_args, **_kwargs):
        return [(None, None, None, None, ("127.0.0.1", 19110))]

    monkeypatch.setattr(loop, "getaddrinfo", loopback_dns)
    with pytest.raises(A2ADiscoveryError, match="requires HTTPS"):
        await _validate_target("http://localhost:19110/card.json", allow_loopback=True)
    with pytest.raises(A2ADiscoveryError, match="private network"):
        await _validate_target("http://localhost:19110/card.json", allow_http=True)

    host, address = await _validate_target(
        "http://localhost:19110/card.json",
        allow_http=True,
        allow_loopback=True,
    )
    assert host == "localhost"
    assert address == "127.0.0.1"


@pytest.mark.asyncio
async def test_allow_http_does_not_allow_public_http(
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio

    loop = asyncio.get_running_loop()

    async def public_dns(*_args, **_kwargs):
        return [(None, None, None, None, ("8.8.8.8", 80))]

    monkeypatch.setattr(loop, "getaddrinfo", public_dns)
    with pytest.raises(A2ADiscoveryError, match="requires HTTPS"):
        await _validate_target(
            "http://agents.example.com/card.json",
            allow_http=True,
            allow_loopback=False,
        )

    host, address = await _validate_target(
        "http://agents.example.com/card.json",
        allow_http=True,
        allow_public_http=True,
    )
    assert host == "agents.example.com"
    assert address == "8.8.8.8"


@pytest.mark.asyncio
@pytest.mark.parametrize("private_address", ["10.0.0.10", "172.16.0.10", "192.168.1.10"])
async def test_private_network_requires_its_discovery_switch(
    monkeypatch: pytest.MonkeyPatch,
    private_address: str,
):
    import asyncio

    loop = asyncio.get_running_loop()

    async def private_dns(*_args, **_kwargs):
        return [(None, None, None, None, (private_address, 8080))]

    monkeypatch.setattr(loop, "getaddrinfo", private_dns)
    with pytest.raises(A2ADiscoveryError, match="private network"):
        await _validate_target(
            "https://weather.internal:8080/card.json",
            allow_http=True,
            allow_loopback=True,
            allow_public_http=True,
        )

    host, address = await _validate_target(
        "https://weather.internal:8080/card.json",
        allow_private_network=True,
    )
    assert host == "weather.internal"
    assert address == private_address

    with pytest.raises(A2ADiscoveryError, match="requires HTTPS"):
        await _validate_target(
            "http://weather.internal:8080/card.json",
            allow_private_network=True,
        )

    await _validate_target(
        "http://weather.internal:8080/card.json",
        allow_http=True,
        allow_private_network=True,
    )


@pytest.mark.asyncio
async def test_private_network_switch_does_not_allow_link_local(
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio

    loop = asyncio.get_running_loop()

    async def link_local_dns(*_args, **_kwargs):
        return [(None, None, None, None, ("169.254.169.254", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", link_local_dns)
    with pytest.raises(A2ADiscoveryError, match="private network"):
        await _validate_target(
            "https://metadata.internal/card.json",
            allow_private_network=True,
        )


@pytest.mark.asyncio
async def test_private_network_switch_does_not_allow_cgnat(
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio

    loop = asyncio.get_running_loop()

    async def cgnat_dns(*_args, **_kwargs):
        return [(None, None, None, None, ("100.100.100.200", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", cgnat_dns)
    with pytest.raises(A2ADiscoveryError, match="private network"):
        await _validate_target(
            "https://metadata.internal/card.json",
            allow_private_network=True,
        )


@pytest.mark.asyncio
async def test_closing_discovery_switches_disables_http_agents(
    manager_api: ManagerApiHarness,
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio

    loop = asyncio.get_running_loop()

    async def test_dns(*args, **_kwargs):
        addresses = {
            "127.0.0.1": "127.0.0.1",
            "weather.internal": "192.168.1.10",
        }
        address = addresses.get(str(args[0]), "8.8.8.8")
        return [(None, None, None, None, (address, 443))]

    monkeypatch.setattr(loop, "getaddrinfo", test_dns)

    async def fetch(url: str, card_path: str | None = None, **_kwargs) -> DiscoveredCard:
        path = card_path or "/.well-known/agent-card.json"
        return DiscoveredCard(
            source_url=url,
            card_path=path,
            card_url=f"{url.rstrip('/')}{path}",
            card_fingerprint=f"sha256:{url}",
            agent_card={"name": url},
            selected_interface={
                "protocol_binding": "JSONRPC",
                "protocol_version": "1.0",
                "url": f"{url.rstrip('/')}/a2a",
            },
        )

    monkeypatch.setattr("manager_server.core.template.a2a_discovery.fetch_agent_card", fetch)
    settings_url = manager_api.templates_url("/a2a-discovery-settings")
    enabled_settings = {
        "allow_http": True,
        "allow_loopback": True,
        "allow_private_network": True,
        "allow_public_http": True,
    }
    response = await manager_api.http.put(settings_url, json=enabled_settings)
    assert response.status_code == 200

    http_agent = await _create_agent(manager_api, url="http://agents.example.com")
    loopback_agent = await _create_agent(manager_api, url="http://127.0.0.1:19110")
    private_agent = await _create_agent(manager_api, url="https://weather.internal:19110")
    https_agent = await _create_agent(manager_api, url="https://secure-agents.example.com")

    response = await manager_api.http.put(
        settings_url,
        json={
            "allow_http": False,
            "allow_loopback": False,
            "allow_private_network": False,
            "allow_public_http": False,
        },
    )
    assert response.status_code == 200

    disabled = await manager_api.get_json(f"/a2a-outbound-templates/{http_agent['template_id']}")
    loopback_disabled = await manager_api.get_json(
        f"/a2a-outbound-templates/{loopback_agent['template_id']}"
    )
    private_disabled = await manager_api.get_json(
        f"/a2a-outbound-templates/{private_agent['template_id']}"
    )
    unchanged = await manager_api.get_json(f"/a2a-outbound-templates/{https_agent['template_id']}")
    assert disabled["enabled"] is False
    assert disabled["last_error_code"] == "DISCOVERY_BLOCKED"
    assert loopback_disabled["enabled"] is False
    assert loopback_disabled["last_error_code"] == "DISCOVERY_BLOCKED"
    assert private_disabled["enabled"] is False
    assert private_disabled["last_error_code"] == "DISCOVERY_BLOCKED"
    assert unchanged["enabled"] is True
