# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MCP entry extensions survive Manager persistence and Gateway publication."""

from __future__ import annotations

import pytest

from conftest import ManagerApiHarness

pytestmark = pytest.mark.unit

_TOKEN_URL = "https://keycloak.example.com/realms/company/protocol/openid-connect/token"
_ENTRY = {
    "name": "customer-tools",
    "transport": "streamable-http",
    "url": "https://egress.example.com/mcp",
    "headers": {"X-Custom-Header": "example"},
    "dynamic_token_url": _TOKEN_URL,
    "options": {"keep": [False, None, {"revision": 1}]},
}


async def _create(h: ManagerApiHarness, entry: dict) -> dict:
    response = await h.http.post(
        "/api/v1/mcp-templates",
        json={"template_name": "mcp-custom-config", "mcp_entry": entry},
    )
    assert response.status_code == 200
    return response.json()["data"]


@pytest.mark.asyncio
async def test_mcp_extensions_survive_round_trip_and_ordinary_edits(manager_api: ManagerApiHarness):
    h = manager_api
    created = await _create(h, _ENTRY)
    path = f"/api/v1/mcp-templates/{created['template_id']}"
    assert created["mcp_entry"] == _ENTRY
    assert (await h.http.get(path)).json()["data"]["mcp_entry"] == _ENTRY
    renamed = await h.http.patch(path, json={"template_name": "renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["data"]["mcp_entry"] == _ENTRY

    changed = {**_ENTRY, "name": "renamed-tools", "headers": {"X-Custom-Header": "updated"}}
    edited = await h.http.patch(path, json={"mcp_entry": changed})
    assert edited.status_code == 200
    assert edited.json()["data"]["mcp_entry"] == changed


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"dynamic_token_url": ""}])
async def test_mcp_without_dynamic_token_url_keeps_existing_contract(manager_api: ManagerApiHarness, extra):
    entry = {key: value for key, value in _ENTRY.items() if key not in {"dynamic_token_url", "options"}}
    entry.update(extra)
    created = await _create(manager_api, entry)
    assert created["mcp_entry"] == entry


@pytest.mark.asyncio
async def test_mcp_extensions_are_published_and_updated_on_gateway(manager_api: ManagerApiHarness):
    h = manager_api
    jid = await h.create_instance()
    await h.handler.update("instance_info", {"jiuwenclaw_id": jid}, {"gateway_status": "online"})
    mcp = await _create(h, _ENTRY)
    tid = mcp["template_id"]
    agent = await h.http.post(
        "/api/v1/agent-templates/",
        json={"template_name": "mcp-custom-agent", "template_ref": {"mcp": [tid]}},
    )
    assert agent.status_code == 200
    published = await h.http.post(
        f"/api/v1/instances/{jid}/agent-resources",
        json={"ref_template_id": agent.json()["data"]["template_id"], "resource_name": "mcp-test",
              "match_exprs": [[]]},
    )
    assert published.status_code == 200
    pushes = [
        body for _jid, method, path, body in h.gateway_sim.calls
        if method == "POST" and path == "/api/v1/mcp-templates"
    ]
    assert pushes[-1]["mcp_entry"] == _ENTRY

    changed = {**_ENTRY, "dynamic_token_url": f"{_TOKEN_URL}?revision=2"}
    edited = await h.http.patch(f"/api/v1/mcp-templates/{tid}", json={"mcp_entry": changed})
    assert edited.status_code == 200
    updates = [
        body for _jid, method, path, body in h.gateway_sim.calls
        if method == "PATCH" and path == f"/api/v1/mcp-templates/{tid}"
    ]
    assert updates[-1]["mcp_entry"] == changed
