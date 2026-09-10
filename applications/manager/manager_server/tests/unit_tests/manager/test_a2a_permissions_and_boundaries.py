"""A2A M5 permission, ownership, and ingress boundary coverage."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import ManagerApiHarness

from manager_server.core.template.a2a_discovery import DiscoveredCard
from manager_server.core.template.push_template_to_gateway import (
    TEMPLATE_KIND_SPECS,
    rebuild_jid_template_ref_for_gateway,
)
from manager_server.infrastructure.utils import utc_now
from manager_server.routers.deps import get_current_user, require_admin

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_a2a_management_routes_require_admin(manager_api: ManagerApiHarness):
    manager_api.app.dependency_overrides.pop(require_admin)

    unauthenticated = await manager_api.http.get(
        manager_api.templates_url("/a2a-outbound-templates")
    )
    assert unauthenticated.status_code == 401

    async def regular_user():
        return SimpleNamespace(user_id="user-1", is_admin=False, groups=[])

    manager_api.app.dependency_overrides[get_current_user] = regular_user
    cases = (
        ("GET", "/a2a-outbound-templates", None),
        ("GET", "/a2a-discovery-settings", None),
        (
            "PUT",
            "/a2a-discovery-settings",
            {
                "allow_http": True,
                "allow_private_network": True,
                "allow_public_http": True,
            },
        ),
        ("POST", "/a2a-outbound-discoveries", {"url": "https://agent.example.com"}),
        ("GET", "/a2a-outbound-templates/agent-1/edit", None),
        (
            "POST",
            "/a2a-outbound-templates",
            {"discovery_id": "candidate-1", "template_name": "Agent"},
        ),
        ("PATCH", "/a2a-outbound-templates/agent-1", {"enabled": False}),
        ("DELETE", "/a2a-outbound-templates/agent-1", None),
        ("POST", "/a2a-outbound-templates/agent-1:refresh", None),
        (
            "POST",
            "/a2a-outbound-templates/agent-1:confirm-revision",
            {"accept": True},
        ),
        ("GET", "/a2a-access-policies", None),
        (
            "POST",
            "/a2a-access-policies",
            {"policy_name": "Policy", "mode": "allowlist"},
        ),
        ("PATCH", "/a2a-access-policies/policy-1", {"enabled": False}),
        ("DELETE", "/a2a-access-policies/policy-1", None),
    )
    for method, path, body in cases:
        response = await manager_api.http.request(
            method,
            manager_api.templates_url(path),
            json=body,
        )
        assert response.status_code == 403, (method, path, response.text)

    # M5 must not silently change the pre-existing Agent template permission contract.
    legacy_agent_update = await manager_api.http.patch(
        manager_api.templates_url("/agent-templates/missing-agent"),
        json={"enabled": False},
    )
    assert legacy_agent_update.status_code == 404


@pytest.mark.asyncio
async def test_gateway_owned_fields_are_rejected_by_manager_contract(
    manager_api: ManagerApiHarness,
):
    discovery = await manager_api.http.post(
        manager_api.templates_url("/a2a-outbound-discoveries"),
        json={"url": "https://agent.example.com", "user_enabled": True},
    )
    assert discovery.status_code == 422

    outbound = await manager_api.http.patch(
        manager_api.templates_url("/a2a-outbound-templates/agent-1"),
        json={"user_enabled": True},
    )
    assert outbound.status_code == 422

    policy = await manager_api.http.post(
        manager_api.templates_url("/a2a-access-policies"),
        json={"policy_name": "Policy", "mode": "allowlist", "history": []},
    )
    assert policy.status_code == 422


@pytest.mark.asyncio
async def test_manager_disable_is_pushed_without_removing_projection(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch
):
    async def fetch(url: str, card_path: str | None = None, **_kwargs) -> DiscoveredCard:
        path = card_path or "/.well-known/agent-card.json"
        return DiscoveredCard(
            source_url=url,
            card_path=path,
            card_url=f"{url.rstrip('/')}{path}",
            card_fingerprint="sha256:m5",
            agent_card={"name": "M5 Agent", "securityRequirements": []},
            selected_interface={
                "protocol_binding": "JSONRPC",
                "protocol_version": "1.0",
                "url": "https://m5-agent.example.com/a2a",
            },
        )

    async def reachable(_handler):
        return ["gateway-m5"]

    monkeypatch.setattr("manager_server.core.template.a2a_discovery.fetch_agent_card", fetch)
    monkeypatch.setattr(
        "manager_server.core.template.push_template_to_gateway.list_reachable_jiuwenclaw_ids",
        reachable,
    )
    discovery = await manager_api.post_json(
        "/a2a-outbound-discoveries", {"url": "https://m5-agent.example.com"}
    )
    outbound = await manager_api.post_json(
        "/a2a-outbound-templates",
        {"discovery_id": discovery["discovery_id"], "template_name": "M5 Agent"},
    )
    policy = await manager_api.post_json(
        "/a2a-access-policies",
        {
            "policy_name": "M5 allowlist",
            "mode": "allowlist",
            "member_template_ids": [outbound["template_id"]],
        },
    )
    agent_response = await manager_api.http.post(
        manager_api.templates_url("/agent-templates/"),
        json={
            "template_name": "M5 bound agent",
            "template_ref": {"a2a_access_policy": [policy["policy_id"]]},
        },
    )
    agent = agent_response.json()["data"]
    now = utc_now()
    await manager_api.handler.create(
        "instance_agent_resource",
        {
            "jiuwenclaw_id": "gateway-m5",
            "resource_id": "resource-m5",
            "resource_name": "M5 bound agent",
            "ref_template_id": agent["template_id"],
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
    )
    await rebuild_jid_template_ref_for_gateway(manager_api.handler, "gateway-m5")

    manager_api.gateway_sim.calls.clear()
    await manager_api.patch_json(
        f"/a2a-outbound-templates/{outbound['template_id']}", {"enabled": False}
    )
    calls = manager_api.gateway_sim.calls
    patch = next(call for call in calls if call[1] == "PATCH")
    assert patch[3]["enabled"] is False
    assert patch[3]["credential"] == {"operation": "keep"}
    assert "user_enabled" not in patch[3]
    assert "history" not in patch[3]
    assert not any(call[1] == "DELETE" for call in calls)


def test_manager_exposes_no_enterprise_a2a_ingress_routes(manager_api: ManagerApiHarness):
    paths = {getattr(route, "path", "") for route in manager_api.app.routes}
    assert not any("a2a-ingress" in path for path in paths)
    assert not any("agent-card.json" in path for path in paths)
    assert "/a2a/" not in paths
    assert not any("ingress" in kind for kind in TEMPLATE_KIND_SPECS)
    assert {kind for kind in TEMPLATE_KIND_SPECS if kind.startswith("a2a_")} == {
        "a2a_outbound_templates",
        "a2a_access_policies",
    }
