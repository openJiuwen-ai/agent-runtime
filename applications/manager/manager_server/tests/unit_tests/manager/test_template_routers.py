# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""模板 CRUD API（template_routers）单元测试。"""

from __future__ import annotations

import pytest

from conftest import ManagerApiHarness
from demo_payloads import (
    container_templates,
    extension_config_templates,
    model_templates,
    mcp_templates,
    service_config_templates,
    skill_prebuilt_templates,
)

pytestmark = pytest.mark.unit

_TEMPLATE_CASES = [
    ("/model-templates", model_templates()[0][1]),
    ("/extension-config-templates", extension_config_templates()[0][1]),
    ("/skill-prebuilt-templates", skill_prebuilt_templates()[0][1]),
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


def _demo_container_wire(container_id: str) -> dict:
    return {
        "container_id": container_id,
        "name": "jiuwenbox",
        "image": "jiuwenclaw/jiuwenbox:latest",
        "imagePullPolicy": "IfNotPresent",
    }
