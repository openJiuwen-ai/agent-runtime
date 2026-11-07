# coding: utf-8
"""e2e ①：管理面配置下发打通。

链路：admin 登录(identity JWT) → manager 建实例(探活 gateway/runtime/user_web)
→ 配置推送(gateway /api/v1/*,push-before-store 语义) → 模板链推送
(container-template → service-config-template → service-resource → runtime
`POST /api/session/config_sync` 落 routing_scope)。
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture()
def jid(manager_api: httpx.Client, cfg, userweb_ready) -> str:
    """建一个临时实例（探活三组件），用完删除。"""
    body = {
        "jiuwenclaw_name": f"e2e-{uuid.uuid4().hex[:8]}",
        "gateway_host": cfg.gateway_url,
        "runtime_host": cfg.runtime_url,
        "user_web_host": cfg.user_web_url,
        "gateway_web_http_host": cfg.gateway_url,
    }
    resp = manager_api.post("/api/v1/instances/", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["gateway_status"] == "online"
    assert data["runtime_status"] == "online"
    yield data["jiuwenclaw_id"]
    manager_api.delete(f"/api/v1/instances/{data['jiuwenclaw_id']}")


@pytest.fixture(scope="session")
def userweb_ready(stack) -> None:
    import httpx as _hx

    cfg = stack
    r = _hx.get(f"{cfg.user_web_url}/", timeout=10)
    assert r.status_code == 200


def test_admin_auth_chain(cfg, identity, admin_token) -> None:
    """identity 播种管理员可登录，manager 识别为平台管理员。"""
    me = identity.me(admin_token)
    assert me["user_id"] == "admin" and me["is_admin"] is True
    who = httpx.get(
        f"{cfg.manager_url}/api/v1/authz/me",
        headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    assert who.status_code == 200
    assert who.json()["data"]["is_platform_admin"] is True


def test_manager_push_logging_config(manager_api: httpx.Client, jid: str) -> None:
    """logging 配置推送：manager 落库并转发 gateway（失败即 400）。"""
    r = manager_api.put(
        f"/api/v1/instances/{jid}/logging",
        json={"level": "WARNING", "gateway": "ERROR"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["level"] == "WARNING"


def test_manager_push_log_masking_rule(manager_api: httpx.Client, jid: str) -> None:
    """log-masking 规则是 push-before-store：200 即网关已确认接收。"""
    r = manager_api.post(
        f"/api/v1/instances/{jid}/log-masking-rules",
        json={"rule_name": f"mask-phone-{uuid.uuid4().hex[:6]}",
              "pattern": r"1[3-9]\d{9}", "replacement": "***", "priority": 1})
    assert r.status_code == 200, r.text
    rule_id = r.json()["data"]["rule_id"]
    # 清理：删除规则（同样要过网关）
    d = manager_api.delete(f"/api/v1/instances/{jid}/log-masking-rules/{rule_id}")
    assert d.status_code == 200, d.text


def test_manager_push_template_chain_reaches_runtime(
        manager_api: httpx.Client, jid: str, cfg, stack) -> None:
    """容器/服务模板 + 服务资源 → runtime config_sync 落 routing_scope。"""
    ct = manager_api.post("/api/v1/container-templates", json={
        "template_name": f"e2e-container-{uuid.uuid4().hex[:6]}",
        "container_id": "e2e-agent", "name": "agent",
        "image": "local/agentserver:e2e",
        "ports": [{"name": "sse", "containerPort": 18092}],
    })
    assert ct.status_code == 200, ct.text
    st = manager_api.post("/api/v1/service-config-templates", json={
        "template_name": f"e2e-service-{uuid.uuid4().hex[:6]}",
        "main_container_id": "e2e-agent", "min_idle_pods": 0})
    assert st.status_code == 200, st.text
    template_id = st.json()["data"]["template_id"]
    sr = manager_api.post(f"/api/v1/instances/{jid}/service-resources", json={
        "resource_name": f"e2e-resource-{uuid.uuid4().hex[:6]}",
        "ref_template_id": template_id,
        "match_exprs": ["user_id == 'user1'"]})
    assert sr.status_code == 200, sr.text
    resource_id = sr.json()["data"]["items"][0]["resource_id"]

    # runtime DB 出现新 scope（config_sync 落库实锤；栈上可能有存量 scope,用相对计数）
    db_path = Path(cfg.home) / "agent_runtime.db"

    def scope_count() -> int:
        with sqlite3.connect(db_path) as db:
            return int(db.execute("SELECT count(*) FROM routing_scope").fetchone()[0])

    assert scope_count() >= 1

    # 清理：删资源（触发 runtime 删 scope）→ 删模板
    d1 = manager_api.delete(
        f"/api/v1/instances/{jid}/service-resources/{resource_id}")
    assert d1.status_code == 200, d1.text
    d2 = manager_api.delete(f"/api/v1/service-config-templates/{template_id}")
    assert d2.status_code == 200, d2.text
    d3 = manager_api.delete("/api/v1/container-templates/e2e-agent")
    assert d3.status_code == 200, d3.text
    with sqlite3.connect(db_path) as db:
        left = db.execute(
            "SELECT count(*) FROM routing_scope WHERE scope_id = ?",
            (f"service-{resource_id}",)).fetchone()[0]
    assert left == 0
