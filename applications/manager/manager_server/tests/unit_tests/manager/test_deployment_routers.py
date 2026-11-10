# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""部署任务 API 测试：defaults / 创建 / 列表 / 详情 / 日志 / 取消（执行器不启动，纯 CRUD 面）。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

_DEPLOYMENTS = "/api/v1/deployments"

# 与工具树校验对齐的最小 env（create 经 defaults 播种路径补全，这里只放关键项）
_BASE_ENV = {"AGENT_RUNTIME_IMAGE": "swr.example/agent-runtime:1.0"}


@pytest.fixture
def tool_available(monkeypatch: pytest.MonkeyPatch):
    """跳过 kubectl/yq/kubeconfig 环境探测（单元测试不依赖宿主机工具）。"""
    from manager_server.core.deployment import service as service_mod

    monkeypatch.setattr(service_mod, "check_tool_available", lambda: (True, ""))


async def test_defaults_seeded_and_masked(manager_api, tool_available):
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/defaults")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is True
    assert "tool_version" in data
    env = data["env"]
    # .env.example 播种：镜像类透出；密码类为空占位不入库（掩码仅对非空值）
    assert env.get("MARIADB_IMAGE")
    assert "CLUSTER_TYPE" not in env   # 白名单外不播种
    for key, value in env.items():
        if "PASSWORD" in key or "SECRET" in key:
            assert value in ("", "******")

    # PUT 写入真实密码后回读为掩码
    resp = await manager_api.http.put(
        f"{_DEPLOYMENTS}/defaults",
        json={"env": {"MARIADB_ROOT_PASSWORD": "Secret@1"}},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["env"]["MARIADB_ROOT_PASSWORD"] == "******"


async def test_defaults_update_and_mask_roundtrip(manager_api, tool_available):
    resp = await manager_api.http.put(
        f"{_DEPLOYMENTS}/defaults",
        json={"env": {"GATEWAY_REPLICAS": "3", "MARIADB_ROOT_PASSWORD": "NewP@ss"}},
    )
    assert resp.status_code == 200
    env = resp.json()["data"]["env"]
    assert env["GATEWAY_REPLICAS"] == "3"
    assert env["MARIADB_ROOT_PASSWORD"] == "******"

    # 掩码值原样传回不覆盖真实密码
    resp = await manager_api.http.put(
        f"{_DEPLOYMENTS}/defaults",
        json={"env": {"MARIADB_ROOT_PASSWORD": "******"}},
    )
    assert resp.status_code == 200

    resp = await manager_api.http.put(
        f"{_DEPLOYMENTS}/defaults",
        json={"env": {"NOT_A_KEY": "x"}},
    )
    assert resp.status_code == 400


async def test_create_list_get_flow(manager_api, tool_available):
    resp = await manager_api.http.post(
        f"{_DEPLOYMENTS}/",
        json={
            "namespace": "ns-flow",
            "jiuwenclaw_name": "flow-instance",
            "deploy_mode": "full",
            "env": _BASE_ENV,
        },
    )
    assert resp.status_code == 200, resp.text
    deployment_id = resp.json()["data"]["deployment_id"]
    assert deployment_id.startswith("dp-")

    resp = await manager_api.http.get(_DEPLOYMENTS + "/", params={"namespace": "ns-flow"})
    assert resp.status_code == 200
    listed = resp.json()["data"]
    assert listed["total"] == 1
    assert listed["items"][0]["deployment_id"] == deployment_id

    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/{deployment_id}")
    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["status"] == "pending"
    assert detail["deploy_mode"] == "full"
    assert "defaults_snapshot" not in detail   # 不外露（含密码）

    # 日志：任务未跑，空集
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/{deployment_id}/logs")
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


async def test_defaults_route_not_swallowed_by_id(manager_api, tool_available):
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/defaults")
    assert resp.status_code == 200
    assert "env" in resp.json()["data"]


async def test_create_conflict_same_namespace(manager_api, tool_available):
    body = {"namespace": "ns-conflict", "jiuwenclaw_name": "a", "env": _BASE_ENV}
    resp = await manager_api.http.post(f"{_DEPLOYMENTS}/", json=body)
    assert resp.status_code == 200
    resp = await manager_api.http.post(f"{_DEPLOYMENTS}/", json=body)
    assert resp.status_code == 409


async def test_create_validation_errors(manager_api, tool_available):
    async def _create(payload: dict) -> int:
        resp = await manager_api.http.post(f"{_DEPLOYMENTS}/", json=payload)
        return resp.status_code

    assert await _create({"namespace": "Bad_NS", "jiuwenclaw_name": "a", "env": _BASE_ENV}) == 400
    assert await _create({"namespace": "ns-v", "jiuwenclaw_name": "a", "env": {"BAD_KEY": "1"}}) == 400
    assert await _create(
        {"namespace": "ns-v", "jiuwenclaw_name": "a", "env": {"GATEWAY_IMAGE": "bad image"}}
    ) == 400
    assert await _create(
        {"namespace": "ns-v", "jiuwenclaw_name": "a", "deploy_mode": "off", "env": _BASE_ENV}
    ) == 422   # pydantic 枚举校验


async def test_cancel_pending_then_recreate_allowed(manager_api, tool_available):
    body = {"namespace": "ns-cancel", "jiuwenclaw_name": "a", "env": _BASE_ENV}
    resp = await manager_api.http.post(f"{_DEPLOYMENTS}/", json=body)
    deployment_id = resp.json()["data"]["deployment_id"]

    resp = await manager_api.http.post(f"{_DEPLOYMENTS}/{deployment_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"

    # 终态再取消 → 409；同 ns 重新创建放行
    resp = await manager_api.http.post(f"{_DEPLOYMENTS}/{deployment_id}/cancel")
    assert resp.status_code == 409
    resp = await manager_api.http.post(f"{_DEPLOYMENTS}/", json=body)
    assert resp.status_code == 200


async def test_get_missing_returns_404(manager_api, tool_available):
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/dp-nothing")
    assert resp.status_code == 404
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/dp-nothing/logs")
    assert resp.status_code == 404
