# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent 模板删除守卫：仍被实例 Agent 引用时拒绝删除。"""

from __future__ import annotations

import pytest
from conftest import ManagerApiHarness

from manager_server.core.instance_resource.instance_agent_resource_service import (
    InstanceAgentResourceService,
)
from manager_server.core.template.push_template_to_gateway import (
    rebuild_jid_template_ref_for_gateway,
    sync_referenced_templates_to_gateway,
)
from manager_server.infrastructure.utils import utc_now

pytestmark = pytest.mark.unit

_JID = "gateway-1"


async def _create_agent_template(h: ManagerApiHarness, **extra: object) -> dict:
    resp = await h.http.post(
        h.templates_url("/agent-templates/"),
        json={"template_name": "Bound agent", **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _bind_agent(
    h: ManagerApiHarness, template_id: str, resource_id: str, *, jid: str = _JID
) -> None:
    now = utc_now()
    await h.handler.create(
        "instance_agent_resource",
        {
            "jiuwenclaw_id": jid,
            "resource_id": resource_id,
            "resource_name": resource_id,
            "ref_template_id": template_id,
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
    )


@pytest.mark.asyncio
async def test_agent_template_delete_blocked_by_agent_references(manager_api):
    h = manager_api
    tid = (await _create_agent_template(h))["template_id"]
    await _bind_agent(h, tid, "resource-1")
    await _bind_agent(h, tid, "resource-2")

    blocked = await h.http.delete(h.templates_url(f"/agent-templates/{tid}"))
    assert blocked.status_code == 400
    assert "cannot delete template: 2 agent reference(s) exist" in blocked.text
    assert "remove references from agent first" in blocked.text

    listed = (await h.http.get(h.templates_url("/agent-templates/"))).json()["data"]
    row = next(item for item in listed["items"] if item["template_id"] == tid)
    assert row["reference_count"] == 2
    # 模板没有被删掉
    detail = await h.http.get(h.templates_url(f"/agent-templates/{tid}"))
    assert detail.status_code == 200
    assert detail.json()["data"]["reference_count"] == 2

    for rid in ("resource-1", "resource-2"):
        await h.handler.delete("instance_agent_resource", {"resource_id": rid})

    ok = await h.http.delete(h.templates_url(f"/agent-templates/{tid}"))
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_rebuild_counts_agent_template_once_for_multiple_resources(manager_api):
    """同一模板在本实例绑 2 个 Agent 资源，rebuild 后 ref_count 必须是 1。"""
    h = manager_api
    skill = await h.post_json(
        "/skill-prebuilt-templates",
        {
            "template_name": "Preset skill",
            "skill_id": "skill-1",
            "source_id": "src-1",
            "version_id": "v1",
        },
    )
    tid = (
        await _create_agent_template(
            h, template_ref={"skill_prebuilt": [skill["template_id"]]}
        )
    )["template_id"]
    await _bind_agent(h, tid, "resource-1")
    await _bind_agent(h, tid, "resource-2")

    await rebuild_jid_template_ref_for_gateway(h.handler, _JID)
    rows = await h.handler.list_records(
        "jid_template_ref", {"jiuwenclaw_id": _JID}, limit=20, offset=0
    )
    counts = {str(row.slot): int(row.ref_count) for row in rows}
    assert counts.get("skill_prebuilt") == 1


@pytest.mark.asyncio
async def test_delete_template_and_skill_after_agent_removed(manager_api):
    """顺序 B 回归：先移除 Agent → 删 Agent 模板成功 → 删 skill 成功。"""
    h = manager_api
    skill = await h.post_json(
        "/skill-prebuilt-templates",
        {
            "template_name": "Preset skill",
            "skill_id": "skill-1",
            "source_id": "src-1",
            "version_id": "v1",
        },
    )
    tid = (
        await _create_agent_template(
            h, template_ref={"skill_prebuilt": [skill["template_id"]]}
        )
    )["template_id"]
    await _bind_agent(h, tid, "resource-1")
    await rebuild_jid_template_ref_for_gateway(h.handler, _JID)

    await InstanceAgentResourceService(h.handler).remove_resource(_JID, "resource-1")

    deleted_template = await h.http.delete(h.templates_url(f"/agent-templates/{tid}"))
    assert deleted_template.status_code == 200, deleted_template.text

    deleted_skill = await h.http.delete(
        h.templates_url(f"/skill-prebuilt-templates/{skill['template_id']}")
    )
    assert deleted_skill.status_code == 200, deleted_skill.text


@pytest.mark.asyncio
async def test_agent_template_gateway_payload_omits_reference_count(manager_api):
    """reference_count 只给 Manager UI，不能随模板下发 Gateway。"""
    h = manager_api
    tid = (await _create_agent_template(h))["template_id"]
    await _bind_agent(h, tid, "resource-1")

    h.gateway_sim.calls.clear()
    await sync_referenced_templates_to_gateway(h.handler, _JID)

    payloads = [
        call[3] for call in h.gateway_sim.calls if call[2] == "/api/v1/agent-templates"
    ]
    assert payloads
    assert all("reference_count" not in payload for payload in payloads)
