# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""``agents.EDPAgent.otel_span_helper`` 的测试桩。

v2.0 §4.2.6：编排层 span 的 context manager 归 agent-store EDPAgent（otel_span_helper.py），
runtime（dispatch.py / remote_agent_handler.py）经 ``from agents.EDPAgent.otel_span_helper
import ...`` 消费。测试环境没有真 EDPAgent，本模块复刻其极简行为供 ``sys.modules`` 注入：
tracer 未注入（``_OTEL_AVAILABLE=False``）时各 cm ``yield None``；注入后经
``_tracer.start_as_current_span`` 建 span 并在进入时设置属性（span 名 / kind / 属性名
与生产签名保持一致）。tracer 注入用 ``_helpers.patch_tracer``。
"""
from __future__ import annotations

import contextlib
import sys
import types


def build_otel_span_helper_stub() -> types.ModuleType:
    """构造 otel_span_helper 桩模块（行为复刻 agent-store 生产实现的最小子集）。"""
    mod = types.ModuleType("agents.EDPAgent.otel_span_helper")
    mod._tracer = None  # type: ignore[attr-defined]
    mod._OTEL_AVAILABLE = False  # type: ignore[attr-defined]

    def _set_tracer(tracer) -> None:
        mod._tracer = tracer  # type: ignore[attr-defined]
        mod._OTEL_AVAILABLE = tracer is not None  # type: ignore[attr-defined]

    def get_tracer():
        return mod._tracer if mod._OTEL_AVAILABLE else None  # type: ignore[attr-defined]

    @contextlib.contextmanager
    def _span(name, kind_str, attrs):
        if not mod._OTEL_AVAILABLE:  # type: ignore[attr-defined]
            yield None
            return
        try:
            from opentelemetry.trace import SpanKind
        except ImportError:
            yield None
            return
        kind = {
            "server": SpanKind.SERVER,
            "client": SpanKind.CLIENT,
        }.get(kind_str, SpanKind.INTERNAL)
        with mod._tracer.start_as_current_span(name, kind=kind) as sp:  # type: ignore[attr-defined]
            for key, value in attrs.items():
                sp.set_attribute(key, value)
            yield sp

    def start_http_request_span(method, route, session_id, trace_id="", agent_id=""):
        attrs = {
            "http.request.method": method,
            "http.route": route,
            "session.id": session_id,
        }
        if trace_id:
            attrs["openjiuwen.trace.id"] = trace_id
        if agent_id:
            attrs["openjiuwen.agent.name"] = agent_id
        return _span("http.request", "server", attrs)

    def start_versatile_adapter_span(query_intent, query_description, session_id,
                                     dispatch_mode="single", workflow_id="",
                                     target_agent="", sub_task_path=""):
        attrs = {
            "openjiuwen.va.dispatch_mode": dispatch_mode,
            "openjiuwen.va.query_intent": query_intent,
            "openjiuwen.va.query_description": query_description,
            "session.id": session_id,
        }
        if workflow_id:
            attrs["openjiuwen.va.workflow_id"] = workflow_id
        if target_agent:
            attrs["openjiuwen.va.target_agent"] = target_agent
        if sub_task_path:
            attrs["openjiuwen.va.sub_task_path"] = sub_task_path
        return _span("service.versatile_adapter", "client", attrs)

    def start_sub_agent_dispatch_span(entity_id, entity_name, query, sub_agent_url,
                                      sub_task_path, context_id, session_id):
        attrs = {
            "openjiuwen.subagent.entity_id": entity_id,
            "openjiuwen.subagent.entity_name": entity_name,
            "openjiuwen.subagent.query": query,
            "openjiuwen.subagent.sub_agent_url": sub_agent_url,
            "openjiuwen.subagent.sub_task_path": sub_task_path,
            "openjiuwen.subagent.context_id": context_id,
            "session.id": session_id,
        }
        return _span("sub_agent.dispatch", "client", attrs)

    mod._set_tracer = _set_tracer  # type: ignore[attr-defined]
    mod.get_tracer = get_tracer  # type: ignore[attr-defined]
    mod.start_http_request_span = start_http_request_span  # type: ignore[attr-defined]
    mod.start_versatile_adapter_span = start_versatile_adapter_span  # type: ignore[attr-defined]
    mod.start_sub_agent_dispatch_span = start_sub_agent_dispatch_span  # type: ignore[attr-defined]
    return mod


def install_otel_span_helper_stub(edp_module) -> types.ModuleType:
    """把 helper 桩挂到 EDPAgent 桩模块下并注册进 sys.modules，返回桩模块。"""
    helper = build_otel_span_helper_stub()
    sys.modules["agents.EDPAgent.otel_span_helper"] = helper
    setattr(edp_module, "otel_span_helper", helper)
    return helper
