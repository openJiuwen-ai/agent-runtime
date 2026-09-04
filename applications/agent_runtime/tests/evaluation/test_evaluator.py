# coding: utf-8
"""Evaluator 单测:disabled/ok/error 三态全链路 + 报告结构与脱敏。"""

from __future__ import annotations

import json

from fakeredis.aioredis import FakeRedis

from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.evaluation.collector import EvaluationCollector
from agent_runtime.evaluation.evaluator import Evaluator
from agent_runtime.evaluation.llm import LLMClient
from agent_runtime.evaluation.state import EvaluationState
from agent_runtime.resource_manager.state import ResourceState
from agent_runtime.session_manager.models import Template
from agent_runtime.session_manager.routing import (
    RoutingScopeDef,
    RoutingSnapshot,
    snapshot_to_json,
)
from agent_runtime.session_manager.state import SessionState


async def _make_evaluator(llm: LLMClient):
    redis = FakeRedis()
    sm_state = SessionState(redis)
    rm_state = ResourceState(redis)
    eval_state = EvaluationState(redis)
    tpl = Template(template_id="t1", scope_concurrency=3, pod_concurrency=2,
                   min_idle_pods=5)   # max_pods 是派生属性:⌈3/2⌉=2
    scope = RoutingScopeDef(scope_id="s1", index=0, template_id="t1",
                            expr="", rule=None)
    snapshot = RoutingSnapshot(
        ver=1, templates={"t1": tpl}, scopes=(scope,))
    await sm_state.write_routing_snapshot(snapshot_to_json(snapshot))
    collector = EvaluationCollector(
        eval_state=eval_state, sm_state=sm_state, rm_state=rm_state,
    )
    await redis.hset("{resource_manager}:resource:scope:s1:config",
                     mapping={"min_idle_pods": "5"})
    arc = AgentRuntimeConfig()
    evaluator = Evaluator(
        collector=collector, llm=llm, state=eval_state, arc=arc,
        instance_id="test-instance",
    )
    return evaluator, eval_state


async def test_disabled_llm_produces_rule_report():
    evaluator, eval_state = await _make_evaluator(LLMClient())
    await evaluator.evaluate_once()
    report = await eval_state.latest_report()
    assert report is not None
    assert report["llm"]["status"] == "disabled"
    assert report["summary"]["active"] == 1
    # min_idle=5 > max_pods=⌈3/2⌉=2 → critical 矛盾 findings
    assert report["summary"]["by_severity"]["critical"] >= 1
    assert any(f["id"] == "S-CONTRADICTION-MIN-IDLE"
               for f in report["findings"])
    assert any("Claw Manager" in c for c in report["caveats"])
    history = await eval_state.list_reports()
    assert len(history) == 1


async def test_llm_ok_merges_additional_findings():
    good = json.dumps({
        "summary": "整体健康",
        "risk_notes": ["r"],
        "additional_findings": [{
            "id": "LLM-1", "severity": "info",
            "target": {"scope_id": "s1", "template_id": "t1"},
            "field": "scope_concurrency", "current": "3", "suggested": "6",
            "rationale": "by llm",
        }],
        "confidence": "medium",
    })

    class _StubLLM(LLMClient):
        async def analyze(self, prompt_payload):
            from agent_runtime.evaluation.llm import LLMResult
            return LLMResult(status="ok", text=good, latency_ms=1.0)

    evaluator, eval_state = await _make_evaluator(
        _StubLLM(base_url="http://x", model="m"))
    await evaluator.evaluate_once()
    report = await eval_state.latest_report()
    assert report["llm"]["status"] == "ok"
    assert report["llm"]["model"] == "m"
    llm_extra = [f for f in report["findings"] if f["source"] == "llm"]
    assert llm_extra and llm_extra[0]["field"] == "scope_concurrency"
    assert report["llm_analysis"]["summary"] == "整体健康"


async def test_llm_error_degrades_to_rule_report():
    class _StubLLM(LLMClient):
        async def analyze(self, prompt_payload):
            from agent_runtime.evaluation.llm import LLMResult
            return LLMResult(status="error", error="HTTPError: 500")

    evaluator, eval_state = await _make_evaluator(
        _StubLLM(base_url="http://x", model="m"))
    await evaluator.evaluate_once()
    report = await eval_state.latest_report()
    assert report["llm"]["status"] == "error"
    assert "500" in report["llm"]["error"]
    assert all(f["source"] == "rule" for f in report["findings"])


async def test_llm_unparseable_output_degrades():
    class _StubLLM(LLMClient):
        async def analyze(self, prompt_payload):
            from agent_runtime.evaluation.llm import LLMResult
            return LLMResult(status="ok", text="这不是JSON", latency_ms=1.0)

    evaluator, eval_state = await _make_evaluator(
        _StubLLM(base_url="http://x", model="m"))
    await evaluator.evaluate_once()
    report = await eval_state.latest_report()
    assert report["llm"]["status"] == "error"
    assert "parse failed" in report["llm"]["error"]


async def test_report_has_no_secrets():
    evaluator, eval_state = await _make_evaluator(LLMClient(
        base_url="http://user:pass@llm.test/v1", model="m",
        api_key="SUPER-SECRET-KEY"))
    await evaluator.evaluate_once()
    report = await eval_state.latest_report()
    text = json.dumps(report, ensure_ascii=False)
    assert "SUPER-SECRET-KEY" not in text
    assert "user:pass" not in text              # base_url 不进报告


async def test_evaluate_failure_does_not_raise():
    class _Boom:
        async def scope_inventory(self):
            raise RuntimeError("redis gone")

    evaluator, eval_state = await _make_evaluator(LLMClient())
    evaluator.collector = _Boom()
    await evaluator.evaluate_once()             # 留痕不炸
    assert await eval_state.latest_report() is None
