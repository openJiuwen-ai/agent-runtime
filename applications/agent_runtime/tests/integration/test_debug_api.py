# coding: utf-8
"""诊断端点（/debug/*）冒烟：local 模式全链路（fakeredis + SQLite + FakeK8s）。

覆盖：overview（含 jobs 计数器）、session/scope/scopes 状态读、config 脱敏、
stats/recent_errors 计数、sysctx 未就绪 503。模式仿 test_http_smoke.py。
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient
from openjiuwen_runtime.service.config import ServiceConfig

from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.main import create_app

SECRET = "SUPERSECRET-KUBECONFIG"


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
    monkeypatch.setenv("AGENT_RUNTIME_SQLITE_PATH", str(tmp_path / "debug.db"))
    monkeypatch.setenv("AGENT_RUNTIME_MODE", "local")
    settings = ServiceConfig.from_env()
    arc = AgentRuntimeConfig.from_env()
    application = create_app(settings, arc)
    return TestClient(application.asgi)


TEMPLATE = {
    "agent_image": "agentserver:debug",
    "namespace": "default",
    "scope_concurrency": 3,
    "pod_concurrency": 2,
    "session_ttl": 60,
    "pod_ttl": 300,
    "min_idle_pods": 0,
    "kubeconfig": SECRET,
}


def _seed_and_route(client):
    """config_sync 建 template + (*) 规则,再 route 一个会话,返回 (session_id, scope_id)。"""
    client.post("/api/session/config_sync", json=_envelope(
        "config_sync", rawdata={
            "kind": "template", "op": "create",
            "template_id": "tpl-debug", **TEMPLATE,
        }))
    client.post("/api/session/config_sync", json=_envelope(
        "config_sync", rawdata={
            "kind": "routing_rule", "op": "create",
            "rule_id": "rule-debug", "group_id": "*", "bot_id": "*",
            "template_id": "tpl-debug",
        }))
    resp = client.post("/api/session/route", json=_envelope(
        "route", session_id="sess-debug", group_id="grp", bot_id="bot"))
    assert resp.status_code == 200, resp.text
    scope_id = hashlib.md5(b"grp\x00bot").hexdigest()
    return "sess-debug", scope_id


def test_debug_overview(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        resp = client.get("/debug/overview")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["instance_id"]
        assert body["mode"] == "local"
        assert isinstance(body["readiness"], dict)
        assert len(body["jobs"]) == 5
        for job in body["jobs"]:
            assert job["interval_sec"] >= 1
            assert "tick_timeout_sec" in job
            assert "ticks" in job and "ok_ticks" in job  # JobRunner snapshot 计数器
            assert "leader" in job  # 可能为 None（tick 间隙锁瞬时缺失）


def test_debug_overview_redacts_kubeconfig(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_KUBECONFIG", "/super/secret/kube/path")
    client = _make_client(tmp_path, monkeypatch)
    with client:
        body = client.get("/debug/overview").json()
        assert body["config"]["kubeconfig"] == "***"


def test_debug_session_found_and_404(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        session_id, scope_id = _seed_and_route(client)

        resp = client.get("/debug/session", params={"session_id": session_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["session"]["scope_id"] == scope_id
        assert body["session"]["pod_id"]
        assert body["ttl_remaining_s"] >= 0
        assert body["scope"]["waiters"] == 0
        assert body["pod"]["sse_url"]

        # 未知会话 → 404；缺参 → 400
        assert client.get(
            "/debug/session", params={"session_id": "nope"}).status_code == 404
        assert client.get("/debug/session").status_code == 400


def test_debug_scope_and_scopes(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        _, scope_id = _seed_and_route(client)

        resp = client.get("/debug/scope", params={"scope_id": scope_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rm"]["pod_count"] >= 1
        assert body["rm"]["total_pods"] >= 1
        assert body["rm"]["pods"], "per-pod 详情非空"
        pod = body["rm"]["pods"][0]
        assert pod["pod_id"] and "phase" in pod
        assert body["sm"]["session_count"] >= 1
        assert SECRET not in resp.text  # scope_config 的 pod_spec_json 已脱敏

        assert client.get(
            "/debug/scope", params={"scope_id": "noscope"}).status_code == 404
        assert client.get("/debug/scope").status_code == 400

        resp = client.get("/debug/scopes")
        assert resp.status_code == 200
        listed = resp.json()["scopes"]
        assert any(s["scope_id"] == scope_id and s["pods"] >= 1 for s in listed)


def test_debug_config_redacts_kubeconfig(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        _seed_and_route(client)
        resp = client.get("/debug/config")
        assert resp.status_code == 200
        assert SECRET not in resp.text
        tpl = next(t for t in resp.json()["templates"]
                   if t["template_id"] == "tpl-debug")
        assert tpl["kubeconfig"] == "***"
        assert any(r["rule_id"] == "rule-debug"
                   for r in resp.json()["routing_rules"])
        assert resp.json()["redis"]["sm_resolve_cache_keys"] >= 1


def test_debug_stats_and_recent_errors(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        # 一个失败 route（缺 session_id → VALIDATION 400）→ 计数进 registry
        resp = client.post("/api/session/route", json=_envelope(
            "route", session_id="", group_id="grp", bot_id="bot"))
        assert resp.status_code == 400

        stats = client.get("/debug/stats").json()
        assert stats["ok"] is True
        endpoint = stats["endpoints"]["route"]
        assert endpoint["error"] >= 1
        assert endpoint["by_error_code"]["VALIDATION"] >= 1
        assert endpoint["total"] >= 1

        errors = client.get("/debug/recent_errors").json()["errors"]
        assert errors and errors[0]["error_code"] == "VALIDATION"
        assert errors[0]["request_id"]


def test_debug_503_when_sysctx_not_ready(tmp_path, monkeypatch):
    """不进 lifespan（无 with）→ sysctx 缺失 → 503 JSON（镜像 healthz 行为）。"""
    client = _make_client(tmp_path, monkeypatch)
    resp = client.get("/debug/overview")
    assert resp.status_code == 503
    assert resp.json()["ok"] is False
