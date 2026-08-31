# coding: utf-8
"""HTTP 冒烟（M6）：真实 App（/api/session，端口语义）+ lifespan + 后台任务。

FastAPI TestClient 驱动 lifespan → OrchestratorSystemContext.start() 拉起
SM/RM 两套上下文与全部 JobRunner；local 模式资源（fakeredis + SQLite +
FakeK8s）。验证 4 个对外端点与错误码契约（HLD §3.1）。
"""

from __future__ import annotations

import time
import uuid

from fastapi.testclient import TestClient
from openjiuwen_runtime.service.config import ServiceConfig

from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.main import create_app


def _envelope(msg_type: str, *, session_id=None, group_id="grp", bot_id="bot",
              user_id="user", rawdata=None):
    return {
        "type": msg_type,
        "metadata": {
            "request_id": f"req-{uuid.uuid4().hex[:8]}",
            "session_id": session_id,
            "user_id": user_id,
            "bot_id": bot_id,
            "extra": {"group_id": group_id},
        },
        "rawdata": rawdata or {},
    }


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_SQLITE_PATH", str(tmp_path / "smoke.db"))
    settings = ServiceConfig.from_env()
    arc = AgentRuntimeConfig(mode="local")
    application = create_app(settings, arc)
    return TestClient(application.asgi)


# 三段式契约(K8s 形态):容器段 + 模板段(只持引用)+ 通配兜底 scope
CONTAINER = {
    "container_id": "c-smoke-main",
    "name": "agent",
    "image": "agentserver:smoke",
    "ports": [{"name": "sse", "containerPort": 8080}],
}
TEMPLATE = {
    "main_container_id": "c-smoke-main",
    "namespace": "default",
    "scope_concurrency": 3,
    "pod_concurrency": 2,
    "session_ttl": 60,
    "pod_ttl": 300,
    "min_idle_pods": 0,
}

# 全量下发：容器 + 模板 + 通配兜底 scope（空 routing_rules）
FULL_SYNC = {
    "containers": [CONTAINER],
    "templates": [{"template_id": "tpl-smoke", **TEMPLATE}],
    "scopes": [{"scope_id": "scope-smoke", "index": 0,
                "template_id": "tpl-smoke", "routing_rules": ""}],
}


def test_http_all_four_endpoints(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        # 1. config_sync：全量下发 template + 通配兜底 scope
        created = client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata=FULL_SYNC))
        assert created.status_code == 200, created.text
        assert created.json()["rawdata"]["ok"] is True
        assert created.json()["rawdata"]["scopes_synced"] == 1

        # 2. route：占额度返回 pod_sse_url / pod_id
        routed = client.post("/api/session/route", json=_envelope(
            "route", session_id="sess-smoke-1"))
        assert routed.status_code == 200, routed.text
        body = routed.json()["rawdata"]
        assert body["pod_id"].startswith("agentserver-")
        assert body["pod_sse_url"].startswith("http://")

        # 3. 幂等回放：同 request_id 重试返回同结果
        env = _envelope("route", session_id="sess-smoke-1")
        first = client.post("/api/session/route", json=env).json()["rawdata"]
        second = client.post("/api/session/route", json=env).json()["rawdata"]
        assert second["pod_id"] == first["pod_id"]

        # 4. touch：保活成功 / 不存在会话 touched=false
        touched = client.post("/api/session/touch", json=_envelope(
            "touch", session_id="sess-smoke-1"))
        assert touched.json()["rawdata"] == {"touched": True}
        missing = client.post("/api/session/touch", json=_envelope(
            "touch", session_id="nope"))
        assert missing.json()["rawdata"] == {"touched": False}

        # 5. 参数缺失 → 400 VALIDATION
        bad = client.post("/api/session/route", json=_envelope(
            "route", session_id=None))
        assert bad.status_code == 400
        assert bad.json()["error_code"] == "VALIDATION"

        # 5b. 缺 user_id → 400 VALIDATION(四参必填)
        bad_user = client.post("/api/session/route", json=_envelope(
            "route", session_id="sess-x", user_id=None))
        assert bad_user.status_code == 400
        assert bad_user.json()["error_code"] == "VALIDATION"

        # 7. cleanup：批删 FakeK8s 里的 AgentServer Pod
        cleaned = client.post("/api/session/cleanup", json=_envelope(
            "cleanup", rawdata={"namespace": "default"}))
        assert cleaned.status_code == 200
        assert cleaned.json()["rawdata"]["cleaned"] >= 1


def test_http_error_contract_corners(tmp_path, monkeypatch):
    """边界错误契约：touch 空 session → 400；cleanup 空目标 → cleaned=0；
    旧 kind/op 协议载荷 → 400（明确拒绝，不触发任何副作用）。"""
    client = _make_client(tmp_path, monkeypatch)
    with client:
        bad_touch = client.post("/api/session/touch", json=_envelope(
            "touch", session_id=None))
        assert bad_touch.status_code == 400
        assert bad_touch.json()["error_code"] == "VALIDATION"

        empty = client.post("/api/session/cleanup", json=_envelope(
            "cleanup", rawdata={"namespace": "no-such-namespace"}))
        assert empty.status_code == 200
        assert empty.json()["rawdata"]["cleaned"] == 0

        legacy = client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={"kind": "nope", "op": "create"}))
        assert legacy.status_code == 400
        assert legacy.json()["error_code"] == "VALIDATION"


def test_http_config_sync_busy_returns_409(tmp_path, monkeypatch):
    """串行化：锁被占 → 409 CONFIG_SYNC_BUSY（可重试）。"""
    client = _make_client(tmp_path, monkeypatch)
    with client:
        # 先建配置；再手工占锁后重发合法全量载荷 → 409
        client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata=FULL_SYNC))
        sysctx = client.app.state.sysctx
        import asyncio

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            sysctx.redis.set(sysctx.sm_config_store.state.k.lock_config_sync(),
                             "held", ex=30)
        )
        busy = client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={
                "containers": FULL_SYNC["containers"],
                "templates": [{"template_id": "tpl-smoke", **TEMPLATE,
                               "session_ttl": 90}],
                "scopes": FULL_SYNC["scopes"],
            }))
        assert busy.status_code == 409
        assert busy.json()["error_code"] == "CONFIG_SYNC_BUSY"


def test_http_route_without_any_scope_is_config_not_found(tmp_path, monkeypatch):
    """只下发模板、不下发任何 scope → 503 CONFIG_NOT_FOUND（不可重试）。"""
    client = _make_client(tmp_path, monkeypatch)
    with client:
        client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={"containers": [CONTAINER],
                                    "templates": [
                {"template_id": "tpl-1", **TEMPLATE}], "scopes": []}))
        no_cfg = client.post("/api/session/route", json=_envelope(
            "route", session_id="sess-x"))
        assert no_cfg.status_code == 503
        assert no_cfg.json()["error_code"] == "CONFIG_NOT_FOUND"
        assert "retry_after" not in no_cfg.json()


def test_background_jobs_run(tmp_path, monkeypatch):
    """lifespan 起的后台 JobRunner 正常运转（sweep tick）且停机干净。"""
    monkeypatch.setenv("AGENT_RUNTIME_SQLITE_PATH", str(tmp_path / "smoke2.db"))
    settings = ServiceConfig.from_env()
    arc = AgentRuntimeConfig(mode="local", sweep_interval=1)
    application = create_app(settings, arc)
    with TestClient(application.asgi) as client:
        client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata=FULL_SYNC))
        client.post("/api/session/route", json=_envelope("route", session_id="s1"))
        time.sleep(2.2)     # 至少一个 sweep tick（RedisAlignedClock + 选主）
        # 会话仍活跃（touch 成功 = sweeper 没把它老化掉）
        touched = client.post("/api/session/touch", json=_envelope("touch",
                                                                   session_id="s1"))
        assert touched.json()["rawdata"] == {"touched": True}


def test_http_healthz_endpoint(tmp_path, monkeypatch):
    """GET /healthz：lifespan 内 200 + instance_id（K8s 探针/多进程就绪轮询用）。"""
    client = _make_client(tmp_path, monkeypatch)
    with client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["instance_id"]  # 非空（hostname:uuid8）
