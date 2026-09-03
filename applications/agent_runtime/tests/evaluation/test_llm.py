# coding: utf-8
"""LLM client 单测:启用判定 / MockTransport 三态 / 解析降级 / 白名单。"""

from __future__ import annotations

import json

import httpx
import pytest

from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.evaluation.llm import (
    LLMClient,
    build_prompt,
    parse_llm_analysis,
)


def test_from_arc_disabled_without_env(monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME_EVAL_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_EVAL_LLM_MODEL", raising=False)
    client = LLMClient.from_arc(AgentRuntimeConfig.from_env())
    assert client.enabled is False


def test_enabled_requires_base_url_and_model():
    assert LLMClient(base_url="http://x/v1", model="m").enabled
    assert not LLMClient(base_url="http://x/v1").enabled
    assert not LLMClient(model="m").enabled


@pytest.mark.asyncio
async def test_analyze_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "m"
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer k"
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "{\"summary\": \"ok\"}"}}]})

    client = LLMClient(base_url="http://llm.test/v1", model="m", api_key="k",
                       transport=httpx.MockTransport(handler))
    result = await client.analyze({"service": {}})
    assert result.status == "ok"
    assert result.text == "{\"summary\": \"ok\"}"


@pytest.mark.asyncio
async def test_analyze_http_error_degrades():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = LLMClient(base_url="http://llm.test/v1", model="m",
                       transport=httpx.MockTransport(handler))
    result = await client.analyze({"service": {}})
    assert result.status == "error"
    assert "500" in result.error or "boom" in result.error


@pytest.mark.asyncio
async def test_analyze_disabled_short_circuits():
    client = LLMClient()      # 未配置
    result = await client.analyze({"service": {}})
    assert result.status == "error"
    assert "disabled" in result.error


def test_parse_fenced_and_bare_json():
    good = {
        "summary": "健康",
        "risk_notes": ["r1"],
        "additional_findings": [{
            "id": "LLM-1", "severity": "warn",
            "target": {"scope_id": "s1", "template_id": "t1"},
            "field": "scope_concurrency", "current": "3", "suggested": "6",
            "rationale": "x",
        }],
        "confidence": "high",
    }
    fenced = "```json\n" + json.dumps(good) + "\n```"
    assert parse_llm_analysis(fenced)["confidence"] == "high"
    assert parse_llm_analysis(json.dumps(good))["summary"] == "健康"


def test_parse_garbage_raises():
    with pytest.raises(ValueError):
        parse_llm_analysis("我认为系统很健康,无需修改。")
    with pytest.raises(ValueError):
        parse_llm_analysis("[1,2,3]")
    with pytest.raises(ValueError):
        parse_llm_analysis("")
    with pytest.raises(ValueError):
        parse_llm_analysis("{\"summary\": \"x\"} trailing")


def test_parse_rejects_out_of_whitelist_fields():
    bad = {
        "summary": "s",
        "additional_findings": [
            {"id": "L1", "severity": "warn", "field": "agent_image",
             "target": {}, "rationale": "换镜像"},
            {"id": "L2", "severity": "warn", "field": "min_idle_pods",
             "target": {"scope_id": "s1"}, "rationale": "ok"},
        ],
    }
    out = parse_llm_analysis(json.dumps(bad))
    fields = [f["field"] for f in out["additional_findings"]]
    assert fields == ["min_idle_pods"]     # agent_image(A 类)整条丢弃


def test_parse_severity_fallback():
    data = {"summary": "s", "additional_findings": [{
        "id": "L", "severity": "apocalyptic", "field": "pod_ttl",
        "target": {}, "rationale": "r",
    }]}
    out = parse_llm_analysis(json.dumps(data))
    assert out["additional_findings"][0]["severity"] == "info"


def test_build_prompt_whitelist_and_truncation():
    payload = {
        "service": {"scope_full_timeout": 30, "evil": "x"},
        "scopes": [{
            "scope_id": "s1", "phase": "active", "scope_concurrency": 3,
            "kubeconfig": "SECRET", "agent_env": {"A": "1"}, "trend": [1, 2, 3],
        }],
        "findings": [{"id": "S-1"}],
    }
    out = build_prompt(payload)
    text = json.dumps(out, ensure_ascii=False)
    assert "SECRET" not in text and "agent_env" not in text and "evil" not in text
    assert "kubeconfig" not in text
    # 体积护栏:超限时 trend 截到只留最新一点
    payload["scopes"] = [{
        "scope_id": f"s{i}", "trend": [{"s": 1}] * 100, "scope_concurrency": 3,
    } for i in range(400)]
    out = build_prompt(payload)
    assert all(len(s.get("trend", [])) <= 1 for s in out["scopes"])
    assert out.get("_truncated") is True
