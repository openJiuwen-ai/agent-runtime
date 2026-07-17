# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""orchestrator.otel_spans 单元测试：三个编排 span 的降级 / 名 / 种类 / 闭环。"""
from __future__ import annotations

import pytest
from opentelemetry.trace import SpanKind

import orchestrator.otel_spans as otel_spans
from tests.framework_parallel._helpers import make_fake_tracer, patch_tracer


# ── tracer=None（OTel 关闭 / 桩环境）：降级为空操作 ─────────────────────────


def test_http_span_yields_none_when_disabled(monkeypatch):
    patch_tracer(monkeypatch, None)
    body_ran = False
    with otel_spans.start_http_request_span("c1") as sp:
        body_ran = True
    assert sp is None
    assert body_ran


@pytest.mark.parametrize(
    "factory",
    [
        otel_spans.start_http_request_span,
        otel_spans.start_versatile_adapter_span,
        otel_spans.start_sub_agent_dispatch_span,
    ],
)
def test_all_spans_degrade_to_none_when_disabled(monkeypatch, factory):
    patch_tracer(monkeypatch, None)
    with factory("c") as sp:
        assert sp is None


def test_disabled_span_does_not_swallow_exception(monkeypatch):
    patch_tracer(monkeypatch, None)
    with pytest.raises(ValueError):
        with otel_spans.start_http_request_span("c"):
            raise ValueError("boom")


# ── tracer 存在：span 名 / kind 正确，session.id 写入 ───────────────────────


@pytest.mark.parametrize(
    "factory,name,kind",
    [
        (otel_spans.start_http_request_span, "http.request", SpanKind.SERVER),
        (otel_spans.start_versatile_adapter_span, "service.versatile_adapter", SpanKind.CLIENT),
        (otel_spans.start_sub_agent_dispatch_span, "sub_agent.dispatch", SpanKind.CLIENT),
    ],
)
def test_span_name_and_kind(monkeypatch, factory, name, kind):
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    with factory("conv-1"):
        pass
    tracer.start_as_current_span.assert_called_once_with(name, kind=kind)


def test_span_records_session_id(monkeypatch):
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    with otel_spans.start_http_request_span("conv-9"):
        pass
    _, _, span = tracer.created[0]
    span.set_attribute.assert_any_call("session.id", "conv-9")


# ── 异常路径：span 仍闭环（__exit__ 被调用，异常向上抛）──────────────────────


def test_span_closes_on_exception(monkeypatch):
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    with pytest.raises(RuntimeError, match="boom"):
        with otel_spans.start_sub_agent_dispatch_span("c"):
            raise RuntimeError("boom")
    _, _, span = tracer.created[0]
    assert span.exited is not None
    assert span.exited[0] is RuntimeError  # 异常透传到 span 的 __exit__


# ── v2.0 丰富 start 属性：各 span 进入时设置业务属性 ─────────────────────────


def test_http_span_sets_http_attributes(monkeypatch):
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    with otel_spans.start_http_request_span(
        "conv", method="POST", route="/v1/x", trace_id="t1", agent_id="edp"
    ):
        pass
    _, _, span = tracer.created[0]
    span.set_attribute.assert_any_call("session.id", "conv")
    span.set_attribute.assert_any_call("http.request.method", "POST")
    span.set_attribute.assert_any_call("http.route", "/v1/x")
    span.set_attribute.assert_any_call("openjiuwen.trace.id", "t1")
    span.set_attribute.assert_any_call("openjiuwen.agent.name", "edp")


def test_va_span_sets_dispatch_mode_and_intent(monkeypatch):
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    with otel_spans.start_versatile_adapter_span(
        "c", query_intent="理财", query_description="推荐", dispatch_mode="parallel", workflow_id="wf1"
    ):
        pass
    _, _, span = tracer.created[0]
    span.set_attribute.assert_any_call("openjiuwen.va.dispatch_mode", "parallel")
    span.set_attribute.assert_any_call("openjiuwen.va.query_intent", "理财")
    span.set_attribute.assert_any_call("openjiuwen.va.query_description", "推荐")
    span.set_attribute.assert_any_call("openjiuwen.va.workflow_id", "wf1")


def test_subagent_span_sets_entity_attributes(monkeypatch):
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    with otel_spans.start_sub_agent_dispatch_span(
        "c", entity_id="A", entity_name="基金 Agent", query="推荐", sub_task_path="['A']"
    ):
        pass
    _, _, span = tracer.created[0]
    span.set_attribute.assert_any_call("openjiuwen.subagent.entity_id", "A")
    span.set_attribute.assert_any_call("openjiuwen.subagent.entity_name", "基金 Agent")
    span.set_attribute.assert_any_call("openjiuwen.subagent.query", "推荐")


def test_set_span_attrs_noop_on_none():
    """tracer 关闭时 span=None，set_span_attrs 不抛（调用方补 response 属性的安全网）。"""
    otel_spans.set_span_attrs(None, {"a": 1})  # 不应抛
