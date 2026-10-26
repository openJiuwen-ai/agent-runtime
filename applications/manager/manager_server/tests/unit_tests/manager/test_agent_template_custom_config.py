"""Agent template custom JSON: API persistence, validation and Gateway synchronization."""

from __future__ import annotations

import pytest

from conftest import ManagerApiHarness

pytestmark = pytest.mark.unit

_CONFIG = [{"xxx": "yyy", "options": {"enabled": True}}, {"xxxz": "asdasd"}]


async def _create(h: ManagerApiHarness, data: dict) -> dict:
    response = await h.http.post(
        "/api/v1/agent-templates/",
        json={"template_name": "custom-config-test", "data": data},
    )
    assert response.status_code == 200
    return response.json()["data"]


@pytest.mark.asyncio
async def test_custom_config_round_trip_omit_and_clear(manager_api: ManagerApiHarness):
    h = manager_api
    data = {"custom_config": _CONFIG, "other_setting": {"keep": True}}
    created = await _create(h, data)
    path = f"/api/v1/agent-templates/{created['template_id']}"
    assert created["data"] == data
    assert (await h.http.get(path)).json()["data"]["data"] == data

    renamed = await h.http.patch(path, json={"template_name": "renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["data"]["data"] == data

    cleared = await h.http.patch(path, json={"data": {**data, "custom_config": []}})
    assert cleared.status_code == 200
    assert cleared.json()["data"]["data"] == {
        "other_setting": {"keep": True}, "custom_config": [],
    }


@pytest.mark.parametrize("invalid", [None, {}, "text", 1, [None], ["text"], [1], [[]]])
@pytest.mark.asyncio
async def test_invalid_custom_config_rejected_on_create_and_update(
    manager_api: ManagerApiHarness, invalid,
):
    h = manager_api
    invalid_data = {"custom_config": invalid}
    rejected = await h.http.post(
        "/api/v1/agent-templates/",
        json={"template_name": "invalid", "data": invalid_data},
    )
    assert rejected.status_code == 422

    created = await _create(h, {"custom_config": _CONFIG})
    path = f"/api/v1/agent-templates/{created['template_id']}"
    rejected = await h.http.patch(path, json={"data": invalid_data})
    assert rejected.status_code == 422
    assert (await h.http.get(path)).json()["data"]["data"]["custom_config"] == _CONFIG


@pytest.mark.asyncio
async def test_custom_config_published_and_updated_on_referencing_gateway(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    jid = await h.create_instance()
    await h.handler.update("instance_info", {"jiuwenclaw_id": jid}, {"gateway_status": "online"})
    created = await _create(h, {"custom_config": _CONFIG})
    template_id = created["template_id"]
    response = await h.http.post(
        f"/api/v1/instances/{jid}/agent-resources",
        json={"ref_template_id": template_id, "resource_name": "published", "match_exprs": [[]]},
    )
    assert response.status_code == 200
    publications = [
        body for _jid, method, path, body in h.gateway_sim.calls
        if method == "POST" and path == "/api/v1/agent-templates"
    ]
    assert publications[-1]["data"]["custom_config"] == _CONFIG

    changed = [{"xxx": "updated"}]
    response = await h.http.patch(
        f"/api/v1/agent-templates/{template_id}", json={"data": {"custom_config": changed}},
    )
    assert response.status_code == 200
    pushes = [
        body for _jid, method, path, body in h.gateway_sim.calls
        if method == "PATCH" and path == f"/api/v1/agent-templates/{template_id}"
    ]
    assert pushes[-1]["data"]["custom_config"] == changed
