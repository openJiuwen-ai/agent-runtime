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
              rawdata=None):
    return {
        "type": msg_type,
        "metadata": {
            "request_id": f"req-{uuid.uuid4().hex[:8]}",
            "session_id": session_id,
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


TEMPLATE = {
    "agent_image": "agentserver:smoke",
    "namespace": "default",
    "scope_concurrency": 3,
    "pod_concurrency": 2,
    "session_ttl": 60,
    "pod_ttl": 300,
    "min_idle_pods": 0,
}


def test_http_all_four_endpoints(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        # 1. config_sync：建 template + 全量路由规则
        created = client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={
                "kind": "template", "op": "create",
                "template_id": "tpl-smoke", "template": TEMPLATE,
            }))
        assert created.status_code == 200, created.text
        assert created.json()["rawdata"]["ok"] is True
        ruled = client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={
                "kind": "routing_rule", "op": "create",
                "rule_id": "rule-all", "group_id": "*", "bot_id": "*",
                "template_id": "tpl-smoke",
            }))
        assert ruled.status_code == 200

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

        # 7. cleanup：批删 FakeK8s 里的 AgentServer Pod
        cleaned = client.post("/api/session/cleanup", json=_envelope(
            "cleanup", rawdata={"namespace": "default"}))
        assert cleaned.status_code == 200
        assert cleaned.json()["rawdata"]["cleaned"] >= 1


def test_http_error_contract_corners(tmp_path, monkeypatch):
    """边界错误契约：touch 空 session → 400；cleanup 空目标 → cleaned=0；
    config_sync 未知 kind → 400（不触发缓存失效副作用）。"""
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

        bad_kind = client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={"kind": "nope", "op": "create"}))
        assert bad_kind.status_code == 400
        assert bad_kind.json()["error_code"] == "VALIDATION"


def test_http_config_sync_busy_returns_409(tmp_path, monkeypatch):
    """串行化：锁被占 → 409 CONFIG_SYNC_BUSY（可重试）。"""
    client = _make_client(tmp_path, monkeypatch)
    with client:
        # 先建一个模板使后续 update 有目标；再手工占锁
        client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={
                "kind": "template", "op": "create",
                "template_id": "tpl-1", "template": TEMPLATE,
            }))
        # 抢占串行化锁：借 route 链路外的原始 redis 不方便，直接再发一次会被
        # 正常处理——改为在持锁场景下断言：通过两个并发请求难以稳定构造，
        # 这里用 app 内 sysctx 直接 SET 锁键
        sysctx = client.app.state.sysctx
        import asyncio

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            sysctx.redis.set(sysctx.sm_config_store.state.k.lock_config_sync(),
                             "held", ex=30)
        )
        busy = client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={
                "kind": "template", "op": "update",
                "template_id": "tpl-1", "updates": {"session_ttl": 90},
            }))
        assert busy.status_code == 409
        assert busy.json()["error_code"] == "CONFIG_SYNC_BUSY"


def test_http_route_without_any_rule_is_config_not_found(tmp_path, monkeypatch):
    """无任何路由规则 → 503 CONFIG_NOT_FOUND（不可重试，无 retry_after）。"""
    client = _make_client(tmp_path, monkeypatch)
    with client:
        client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={
                "kind": "template", "op": "create",
                "template_id": "tpl-1", "template": TEMPLATE,
            }))
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
            "config_sync", rawdata={
                "kind": "template", "op": "create",
                "template_id": "tpl-1", "template": TEMPLATE,
            }))
        client.post("/api/session/config_sync", json=_envelope(
            "config_sync", rawdata={
                "kind": "routing_rule", "op": "create", "rule_id": "r",
                "group_id": "*", "bot_id": "*", "template_id": "tpl-1",
            }))
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
