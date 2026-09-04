# coding: utf-8
"""诊断端点（/visualization/*）冒烟：local 模式全链路（fakeredis + SQLite + FakeK8s）。

覆盖：overview（含 jobs 计数器）、session/scope/scopes 状态读（含 2026-09
容量闸门字段）、config 脱敏、stats/recent_errors 计数与 per-scope 聚合段、
history/evaluation（自评估数据层；异步用例直接驱动 sample/evaluate_once）、
sysctx 未就绪 503。同步用例仿 test_http_smoke.py，异步用例仿 _dual_harness。
"""

from __future__ import annotations

import uuid

import httpx
from fastapi.testclient import TestClient
from openjiuwen_runtime.service.config import ServiceConfig

from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.main import create_app

SECRET = "SUPERSECRET-KUBECONFIG"
SCOPE_ID = "scope-debug"   # 播种的通配兜底 scope_id


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
    """config_sync 全量下发 template + 通配兜底 scope,再 route 一个会话。"""
    from tests.conftest import split_sync_payload

    client.post("/api/session/config_sync", json=_envelope(
        "config_sync", rawdata=split_sync_payload(
            [{"template_id": "tpl-debug", **TEMPLATE}],
            [{"scope_id": SCOPE_ID, "index": 0,
              "template_id": "tpl-debug", "routing_rules": ""}],
        )))
    resp = client.post("/api/session/route", json=_envelope(
        "route", session_id="sess-debug", group_id="grp", bot_id="bot"))
    assert resp.status_code == 200, resp.text
    return "sess-debug", SCOPE_ID


def test_visualization_overview(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        resp = client.get("/visualization/overview")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["instance_id"]
        assert body["mode"] == "local"
        assert isinstance(body["readiness"], dict)
        # 2026-09 起 7 个:5 个编排 job + sys_sample/sys_eval(自评估)
        assert len(body["jobs"]) == 7
        job_names = {job["name"] for job in body["jobs"]}
        assert {"sys_sample", "sys_eval"} <= job_names
        for job in body["jobs"]:
            assert job["interval_sec"] >= 1
            assert "tick_timeout_sec" in job
            assert "ticks" in job and "ok_ticks" in job  # JobRunner snapshot 计数器
            assert "leader" in job  # 可能为 None（tick 间隙锁瞬时缺失）
        cfg = body["config"]
        assert cfg["eval_sample_interval"] >= 5
        assert cfg["eval_interval"] >= 30
        assert cfg["eval_llm_enabled"] is False  # env 未配置 → 禁用
        assert cfg["eval_pod_budget"] == 0


def test_visualization_overview_redacts_kubeconfig(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_KUBECONFIG", "/super/secret/kube/path")
    client = _make_client(tmp_path, monkeypatch)
    with client:
        body = client.get("/visualization/overview").json()
        assert body["config"]["kubeconfig"] == "***"


def test_visualization_session_found_and_404(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        session_id, scope_id = _seed_and_route(client)

        resp = client.get("/visualization/session", params={"session_id": session_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["session"]["scope_id"] == scope_id
        assert body["session"]["pod_id"]
        assert body["ttl_remaining_s"] >= 0
        assert body["pod"]["sse_url"]

        # 未知会话 → 404；缺参 → 400
        assert client.get(
            "/visualization/session", params={"session_id": "nope"}).status_code == 404
        assert client.get("/visualization/session").status_code == 400


def test_visualization_scope_and_scopes(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        _, scope_id = _seed_and_route(client)

        resp = client.get("/visualization/scope", params={"scope_id": scope_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rm"]["pod_count"] >= 1
        assert body["rm"]["total_pods"] >= 1
        assert body["rm"]["pods"], "per-pod 详情非空"
        pod = body["rm"]["pods"][0]
        assert pod["pod_id"] and "phase" in pod
        assert body["sm"]["session_count"] >= 1
        assert SECRET not in resp.text  # scope_config 的 pod_spec_json 已脱敏

        # 2026-09 容量闸门:scope 维度看得到 SM 侧策略与派生链
        assert body["phase"] == "active"
        capacity = body["sm"]["capacity"]
        assert capacity["template_id"] == "tpl-debug"
        assert capacity["scope_concurrency"] == 3
        assert capacity["max_pods"] == 2               # ⌈3/2⌉
        assert capacity["session_utilization"] is not None
        assert capacity["route_budget_sec"] > 0        # ready_timeout + 10(快失败后无队列)

        assert client.get(
            "/visualization/scope", params={"scope_id": "noscope"}).status_code == 404
        assert client.get("/visualization/scope").status_code == 400

        resp = client.get("/visualization/scopes")
        assert resp.status_code == 200
        listed = resp.json()["scopes"]
        row = next(s for s in listed if s["scope_id"] == scope_id)
        assert row["pods"] >= 1
        assert row["phase"] == "active"
        assert row["template_id"] == "tpl-debug"
        assert row["scope_concurrency"] == 3
        assert row["session_count"] >= 1
        assert row["max_pods"] == 2


def test_visualization_config_redacts_kubeconfig(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        _seed_and_route(client)
        resp = client.get("/visualization/config")
        assert resp.status_code == 200
        assert SECRET not in resp.text
        tpl = next(t for t in resp.json()["templates"]
                   if t["template_id"] == "tpl-debug")
        assert tpl["kubeconfig"] == "***"
        assert any(s["scope_id"] == SCOPE_ID and s["routing_rules"] == ""
                   for s in resp.json()["routing_scopes"])
        assert resp.json()["routing_snapshot"]["exists"] is True
        assert resp.json()["routing_snapshot"]["scope_count"] >= 1


def test_visualization_stats_and_recent_errors(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    with client:
        # 一个失败 route（缺 session_id → VALIDATION 400）→ 计数进 registry
        resp = client.post("/api/session/route", json=_envelope(
            "route", session_id="", group_id="grp", bot_id="bot"))
        assert resp.status_code == 400

        stats = client.get("/visualization/stats").json()
        assert stats["ok"] is True
        endpoint = stats["endpoints"]["route"]
        assert endpoint["error"] >= 1
        assert endpoint["by_error_code"]["VALIDATION"] >= 1
        assert endpoint["total"] >= 1

        errors = client.get("/visualization/recent_errors").json()["errors"]
        assert errors and errors[0]["error_code"] == "VALIDATION"
        assert errors[0]["request_id"]


def test_visualization_503_when_sysctx_not_ready(tmp_path, monkeypatch):
    """不进 lifespan（无 with）→ sysctx 缺失 → 503 JSON（镜像 healthz 行为）。"""
    client = _make_client(tmp_path, monkeypatch)
    resp = client.get("/visualization/overview")
    assert resp.status_code == 503
    assert resp.json()["ok"] is False


# ------------------------------------------------------------ 自评估数据层(异步,直接驱动内部 on_tick)


async def _seed_and_route_async(client: httpx.AsyncClient) -> tuple[str, str]:
    from tests.conftest import split_sync_payload

    resp = await client.post("/api/session/config_sync", json=_envelope(
        "config_sync", rawdata=split_sync_payload(
            [{"template_id": "tpl-debug", **TEMPLATE}],
            [{"scope_id": SCOPE_ID, "index": 0,
              "template_id": "tpl-debug", "routing_rules": ""}],
        )))
    assert resp.status_code == 200, resp.text
    resp = await client.post("/api/session/route", json=_envelope(
        "route", session_id="sess-debug", group_id="grp", bot_id="bot"))
    assert resp.status_code == 200, resp.text
    return "sess-debug", SCOPE_ID


async def test_visualization_history_evaluation_and_scope_stats(tmp_path, monkeypatch):
    """history/evaluation/stats.scopes:采样与评估直接驱动 on_tick(不等 tick)。"""
    from tests.integration._dual_harness import asgi_lifespan

    monkeypatch.setenv("AGENT_RUNTIME_SQLITE_PATH", str(tmp_path / "eval.db"))
    monkeypatch.setenv("AGENT_RUNTIME_MODE", "local")
    settings = ServiceConfig.from_env()
    arc = AgentRuntimeConfig.from_env()
    application = create_app(settings, arc)
    async with asgi_lifespan(application.asgi):
        sysctx = application.asgi.state.sysctx
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application.asgi),
            base_url="http://eval.test",
        ) as client:
            _, scope_id = await _seed_and_route_async(client)

            # ---- evaluation:无报告属正常态(间隔未到),不 404
            resp = await client.get("/visualization/evaluation")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["latest"] is None and body["history"] == []

            # ---- per-scope 计数:flush 落 Redis 后 stats.scopes 全局可见
            await sysctx._flush_telemetry_once()
            stats = (await client.get("/visualization/stats")).json()
            counters = stats["scopes"].get(scope_id, {})
            assert counters.get("route_total", 0) >= 1
            assert counters.get("route_ok", 0) >= 1

            # ---- history:驱动一轮采样 → 窗口内可读
            await sysctx.eval_collector.sample_once()
            resp = await client.get(
                "/visualization/history", params={"scope_id": scope_id})
            assert resp.status_code == 200, resp.text
            hist = resp.json()
            assert hist["scope_id"] == scope_id
            assert hist["points"], "采样后窗口内必有数据点"
            assert hist["points"][0]["s"] >= 1     # 新在前;route 过 → session≥1
            assert hist["counters_current"].get("route_total", 0) >= 1
            assert (await client.get("/visualization/history")).status_code == 400

            # ---- evaluation:驱动一轮评估 → 报告落 Redis,任意副本同读
            await sysctx.evaluator.evaluate_once()
            resp = await client.get("/visualization/evaluation")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            latest = body["latest"]
            assert latest is not None
            assert latest["llm"]["status"] == "disabled"   # env 未配置 → 纯规则
            assert isinstance(latest["findings"], list)
            assert latest["summary"]["scopes_total"] >= 1
            assert latest["summary"]["active"] >= 1
            assert any("Claw Manager" in c for c in latest["caveats"])
            assert body["history"], "历史瘦身条目非空"
            assert "findings" not in body["history"][0]

            # ---- 停机韧性:stop 后(模拟)报告仍在 Redis —— 由 lifespan 退出隐式覆盖
