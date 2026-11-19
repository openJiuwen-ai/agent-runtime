# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""模板 CRUD API（template_routers）单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from conftest import ManagerApiHarness
from demo_payloads import (
    container_templates,
    extension_config_templates,
    model_templates,
    mcp_templates,
    service_config_templates,
    expert_package_templates,
    skill_prebuilt_templates,
)

pytestmark = pytest.mark.unit

_TEMPLATE_CASES = [
    ("/model-templates", model_templates()[0][1]),
    ("/extension-config-templates", extension_config_templates()[0][1]),
    ("/skill-prebuilt-templates", skill_prebuilt_templates()[0][1]),
    ("/expert-package-templates", expert_package_templates()[0][1]),
    ("/mcp-templates", mcp_templates()[0][1]),
    ("/service-config-templates", service_config_templates()[0][1]),
]


@pytest.mark.parametrize("path,create_body", _TEMPLATE_CASES)
@pytest.mark.asyncio
async def test_template_crud_lifecycle(
    manager_api: ManagerApiHarness,
    path: str,
    create_body: dict,
):
    h = manager_api

    created = await h.post_json(path, create_body)
    template_id = created["template_id"]
    assert template_id
    assert created["template_name"] == create_body["template_name"]

    fetched = await h.get_json(f"{path}/{template_id}")
    assert fetched["template_id"] == template_id

    listed = await h.get_json(path, page=1, page_size=50)
    assert listed["total"] >= 1
    assert template_id in {item["template_id"] for item in listed["items"]}

    patched = await h.patch_json(
        f"{path}/{template_id}",
        {"enabled": False},
    )
    assert patched["enabled"] is False

    await h.delete_ok(f"{path}/{template_id}")

    missing = await h.http.get(h.templates_url(f"{path}/{template_id}"))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_model_template_list_filter_by_model_type(manager_api: ManagerApiHarness):
    h = manager_api
    for _key, body in model_templates():
        await h.post_json("/model-templates", body)

    vision_rows = await h.get_json("/model-templates", model_type="vision")
    assert vision_rows["total"] >= 1
    for item in vision_rows["items"]:
        assert "vision" in item["model_type"]


@pytest.mark.asyncio
async def test_extension_config_template_list_filter(manager_api: ManagerApiHarness):
    h = manager_api
    for _key, body in extension_config_templates():
        await h.post_json("/extension-config-templates", body)

    gateway_hooks = await h.get_json(
        "/extension-config-templates",
        component="gateway",
        hook_type="pre_request",
    )
    assert gateway_hooks["total"] >= 1
    for item in gateway_hooks["items"]:
        assert item["component"] == "gateway"
        assert item["hook_type"] == "pre_request"


# --- service_config_container（容器模板）---


@pytest.mark.asyncio
async def test_container_template_crud_lifecycle(manager_api: ManagerApiHarness):
    h = manager_api
    _key, create_body = container_templates()[0]

    created = await h.post_json("/container-templates", create_body)
    template_id = created["template_id"]
    assert template_id
    assert created["container_id"] == create_body["container_id"]
    assert created["reference_count"] == 0

    fetched = await h.get_json(f"/container-templates/{template_id}")
    assert fetched["template_id"] == template_id

    listed = await h.get_json("/container-templates", page=1, page_size=50)
    assert listed["total"] >= 1
    assert template_id in {item["template_id"] for item in listed["items"]}

    patched = await h.patch_json(
        f"/container-templates/{template_id}", {"template_name": "改名后的容器模板"}
    )
    assert patched["template_name"] == "改名后的容器模板"

    await h.delete_ok(f"/container-templates/{template_id}")
    missing = await h.http.get(h.templates_url(f"/container-templates/{template_id}"))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_container_template_delete_blocked_when_bound(manager_api: ManagerApiHarness):
    """容器模板被运行时模板绑定时删除须 400。"""
    from httpx import codes

    h = manager_api
    c_key, container_body = container_templates()[0]
    container = await h.post_json("/container-templates", container_body)

    s_key, svc_body = service_config_templates()[0]
    await h.post_json("/service-config-templates", svc_body)

    resp = await h.http.delete(
        h.templates_url(f"/container-templates/{container['template_id']}")
    )
    assert resp.status_code == codes.BAD_REQUEST


@pytest.mark.asyncio
async def test_service_config_template_rejects_unknown_container_binding(manager_api: ManagerApiHarness):
    """运行时模板绑定不存在的容器模板须 400。"""
    from httpx import codes

    h = manager_api
    _key, svc_body = service_config_templates()[0]
    body = {**svc_body, "main_container_id": "c-not-exist"}
    resp = await h.http.post(h.templates_url("/service-config-templates"), json=body)
    assert resp.status_code == codes.BAD_REQUEST


@pytest.mark.asyncio
async def test_service_config_template_out_bound_containers(manager_api: ManagerApiHarness):
    """Out 投影返回 bound_containers 绑定摘要，data 不再内联 containers。"""
    h = manager_api
    for _key, body in container_templates():
        await h.post_json("/container-templates", body)

    s_key, svc_body = service_config_templates()[0]
    svc_body = {
        **svc_body,
        "sidecar_container_ids": ["c-jiuwenbox"],
        "data": {
            "config_sync": {
                "containers": [_demo_container_wire("c-jiuwenbox")],
            },
        },
    }
    created = await h.post_json("/service-config-templates", svc_body)

    briefs = created["bound_containers"]
    assert briefs is not None
    by_cid = {b["container_id"]: b for b in briefs}
    assert set(by_cid) == {"c-agentserver", "c-jiuwenbox"}
    assert by_cid["c-agentserver"]["template_name"] == "默认 AgentServer 容器"
    assert by_cid["c-jiuwenbox"]["image"] == "jiuwenclaw/jiuwenbox:latest"

    # data 不再回填容器列表
    sync = (created.get("data") or {}).get("config_sync") or {}
    assert "containers" not in sync

    # 容器模板引用计数
    listed = await h.get_json("/container-templates", page=1, page_size=50)
    ref_by_cid = {
        item["container_id"]: item["reference_count"] for item in listed["items"]
    }
    assert ref_by_cid.get("c-agentserver") == 1
    assert ref_by_cid.get("c-jiuwenbox") == 1


@pytest.mark.asyncio
async def test_expert_package_template_rejects_hidden_hub_asset(manager_api: ManagerApiHarness):
    resp = await manager_api.http.post(
        manager_api.templates_url("/expert-package-templates"),
        json={
            "template_name": "隐藏专家",
            "packages": [
                {
                    "kind": "agent_template",
                    "asset_id": "794db1bfe8884d6897d62d3584894a59",
                },
            ],
        },
    )
    assert resp.status_code == 422
    assert "hidden hub asset" in resp.text


@pytest.mark.asyncio
async def test_expert_package_template_revision_occ_returns_409(
    manager_api: ManagerApiHarness,
):
    """创建 revision=0；正确 expected_revision 保存涨到 1；再用 0 保存应 409。"""
    h = manager_api
    created = await h.post_json("/expert-package-templates", expert_package_templates()[0][1])
    template_id = created["template_id"]
    assert created["revision"] == 0

    first = await h.patch_json(
        f"/expert-package-templates/{template_id}",
        {
            "packages": [
                {
                    "kind": "agent_template",
                    "asset_id": "sales-expert",
                    "version": "1.0.0",
                },
                {
                    "kind": "agent_group",
                    "asset_id": "sales-team",
                    "version": "2.0.0",
                },
                {
                    "kind": "agent_template",
                    "asset_id": "extra-expert",
                    "version": "1.0.0",
                },
            ],
            "expected_revision": 0,
        },
    )
    assert first["revision"] == 1
    assert {item["asset_id"] for item in first["packages"]} == {
        "sales-expert",
        "sales-team",
        "extra-expert",
    }

    resp = await h.http.patch(
        h.templates_url(f"/expert-package-templates/{template_id}"),
        json={
            "packages": [
                {
                    "kind": "agent_template",
                    "asset_id": "sales-expert",
                    "version": "1.0.0",
                },
            ],
            "expected_revision": 0,
        },
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "stale_template"

    current = await h.get_json(f"/expert-package-templates/{template_id}")
    assert current["revision"] == 1
    assert {item["asset_id"] for item in current["packages"]} == {
        "sales-expert",
        "sales-team",
        "extra-expert",
    }


@pytest.mark.asyncio
async def test_expert_package_template_enabled_toggle_bumps_revision(
    manager_api: ManagerApiHarness,
):
    """仅 PATCH enabled 也涨 revision；旧 revision 编辑 409；用开关返回值编辑 200。"""
    h = manager_api
    created = await h.post_json("/expert-package-templates", expert_package_templates()[0][1])
    template_id = created["template_id"]
    assert created["revision"] == 0

    toggled = await h.patch_json(
        f"/expert-package-templates/{template_id}",
        {"enabled": False},
    )
    assert toggled["enabled"] is False
    assert toggled["revision"] == 1

    resp = await h.http.patch(
        h.templates_url(f"/expert-package-templates/{template_id}"),
        json={
            "template_name": created["template_name"],
            "packages": created["packages"],
            "expected_revision": 0,
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "stale_template"

    ok = await h.patch_json(
        f"/expert-package-templates/{template_id}",
        {
            "template_name": created["template_name"],
            "packages": created["packages"],
            "expected_revision": toggled["revision"],
        },
    )
    assert ok["template_id"] == template_id
    assert ok["revision"] == 2


@pytest.mark.asyncio
async def test_expert_package_template_concurrent_same_revision(
    manager_api: ManagerApiHarness,
):
    """同 expected_revision 并发保存：一成一 409，库内容属于成功方。"""
    h = manager_api
    created = await h.post_json("/expert-package-templates", expert_package_templates()[0][1])
    template_id = created["template_id"]
    assert created["revision"] == 0

    body_a = {
        "template_name": "concurrent-winner-a",
        "packages": [
            {
                "kind": "agent_template",
                "asset_id": "sales-expert",
                "version": "1.0.0",
            },
        ],
        "expected_revision": 0,
    }
    body_b = {
        "template_name": "concurrent-winner-b",
        "packages": [
            {
                "kind": "agent_group",
                "asset_id": "sales-team",
                "version": "2.0.0",
            },
        ],
        "expected_revision": 0,
    }

    resp_a, resp_b = await asyncio.gather(
        h.http.patch(
            h.templates_url(f"/expert-package-templates/{template_id}"),
            json=body_a,
        ),
        h.http.patch(
            h.templates_url(f"/expert-package-templates/{template_id}"),
            json=body_b,
        ),
    )
    statuses = {resp_a.status_code, resp_b.status_code}
    assert statuses == {200, 409}
    loser = resp_a if resp_a.status_code == 409 else resp_b
    winner = resp_a if resp_a.status_code == 200 else resp_b
    assert loser.json()["detail"]["code"] == "stale_template"
    winner_data = winner.json()["data"]
    assert winner_data["revision"] == 1

    current = await h.get_json(f"/expert-package-templates/{template_id}")
    assert current["revision"] == 1
    assert current["template_name"] == winner_data["template_name"]
    assert {item["asset_id"] for item in current["packages"]} == {
        item["asset_id"] for item in winner_data["packages"]
    }


def _demo_container_wire(container_id: str) -> dict:
    return {
        "container_id": container_id,
        "name": "jiuwenbox",
        "image": "jiuwenclaw/jiuwenbox:latest",
        "imagePullPolicy": "IfNotPresent",
    }
