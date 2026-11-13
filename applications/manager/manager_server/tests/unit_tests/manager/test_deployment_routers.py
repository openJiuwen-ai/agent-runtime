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


async def test_defaults_seeded_and_masked(manager_api, tool_available, monkeypatch):
    # 播种源固定注入（.env.custom 唯一真源；不依赖仓里可能存在的未跟踪文件）
    from manager_server.core.deployment import service as service_mod

    monkeypatch.setattr(
        service_mod,
        "parse_env_custom",
        lambda *a, **k: {
            "MARIADB_IMAGE": "swr.example/mariadb-amd64:11.8",
            "MANAGER_SERVER_IMAGE": "swr.example/manager-server:15y",
            "MARIADB_ROOT_PASSWORD": "Root@123456",
            "CLUSTER_TYPE": "k8s",   # 白名单外 → 不播种
        },
    )
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/defaults")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is True
    assert "tool_version" in data
    env = data["env"]
    assert env["MARIADB_IMAGE"] == "swr.example/mariadb-amd64:11.8"
    assert "CLUSTER_TYPE" not in env   # 白名单外不播种
    assert "MANAGER_SERVER_IMAGE" not in env   # 管理面镜像不展示（产品面才经部署）
    assert "MANAGER_WEB_REPLICAS" not in env   # 管理面副本同样不展示
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

    # PUT 之后存储不得混入强制键（历史 bug：混入后 POST 创建任务报"不支持的配置键"）
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/defaults")
    stored = resp.json()["data"]["env"]
    assert "CLUSTER_TYPE" not in stored
    assert "MODE" not in stored


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
    assert detail["action"] == "up"
    assert detail["deploy_mode"] == "full"
    # deploy_mode 必须持久化进 params.env（执行器按 defaults+params.env 重新 merge）
    assert detail["params"]["env"]["AGENT_RUNTIME_DEPLOY_MODE"] == "full"
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


async def test_defaults_sync_from_snapshot(manager_api, tool_available, monkeypatch):
    """manager up 注入的快照在启动同步时覆盖 defaults（开箱即用链路）。"""
    from manager_server.core.deployment import service as service_mod
    from manager_server.infrastructure.config import settings

    monkeypatch.setattr(
        settings,
        "deploy_env_snapshot",
        '{"GATEWAY_IMAGE":"swr.example/gw:16y","MARIADB_ROOT_PASSWORD":"should-drop",'
        '"EVIL_KEY":"x","DB_TYPE":"mysql"}',
    )
    synced = await service_mod.sync_defaults_from_snapshot(manager_api.handler)
    assert synced == 2   # 敏感键与白名单外键被剔除

    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/defaults")
    env = resp.json()["data"]["env"]
    assert env["GATEWAY_IMAGE"] == "swr.example/gw:16y"
    assert env["DB_TYPE"] == "mysql"
    assert "MARIADB_ROOT_PASSWORD" not in env   # 注入侧已剔除敏感键
    assert "EVIL_KEY" not in env

    # 二次同步：覆盖旧值（版本升级跟进），操作员自配键保留
    monkeypatch.setattr(
        settings, "deploy_env_snapshot", '{"GATEWAY_IMAGE":"swr.example/gw:17y"}'
    )
    await manager_api.http.put(
        f"{_DEPLOYMENTS}/defaults", json={"env": {"WEB_REPLICAS": "2"}}
    )
    await service_mod.sync_defaults_from_snapshot(manager_api.handler)
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/defaults")
    env = resp.json()["data"]["env"]
    assert env["GATEWAY_IMAGE"] == "swr.example/gw:17y"   # 跟进新版
    assert env["WEB_REPLICAS"] == "2"                     # 操作员键保留


async def test_create_down_action_task(manager_api, tool_available):
    resp = await manager_api.http.post(
        f"{_DEPLOYMENTS}/",
        json={
            "namespace": "ns-down",
            "jiuwenclaw_name": "down-instance",
            "action": "down",
            "env": _BASE_ENV,
        },
    )
    assert resp.status_code == 200, resp.text
    deployment_id = resp.json()["data"]["deployment_id"]
    resp = await manager_api.http.get(f"{_DEPLOYMENTS}/{deployment_id}")
    detail = resp.json()["data"]
    assert detail["action"] == "down"
    assert detail["params"]["action"] == "down"
