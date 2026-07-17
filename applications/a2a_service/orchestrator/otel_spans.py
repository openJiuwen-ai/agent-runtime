# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""编排层 OpenTelemetry span（归 agent-runtime / a2a_service）。

SDK（openjiuwen.tracer_otel）自动盖住的 chain / llm / 正常 tool span 由 EDPAgent
产出；这里只补 SDK 不感知的**编排层** span：HTTP 入口、VersatileAdapter 出站调用、
子 Agent 并行派发。三者经唯一跨仓契约 ``agents.EDPAgent.get_otel_tracer()`` 复用
EDPAgent 持有的 TracerProvider，使整条 trace 同根（设计文档 §2.4/§5.0 实证桥接成立）。

属性规范对齐 EDPAgent_OTel_实现方案与评估 v2.0 §3.2/3.3：
  - ``start_*`` 属性（intent/entity_id/dispatch_mode 等）在 cm 进入时由本模块设置；
  - ``response`` 属性（response_summary/cost/elapsed_ms/status/child_task_id 等）
    在调用方拿到结果后、``with`` 退出前，通过 yield 出来的 span 对象补设。

设计要点：
  - **零侵入降级**：tracer 为 None（OTel 关闭 / 未装 / 桩环境）时，context manager
    ``yield None`` 并照常执行 body（含 ``await``），不抛、不建 span。
  - **懒导入**：``opentelemetry`` 与 ``agents.EDPAgent`` 都在函数体内导入——模块顶层
    仅 ``import contextlib``，保证 import 安全、现有测试（tracer=None）永不触碰
    opentelemetry，全量回归无需安装 opentelemetry。
  - **同步 cm 包异步 body**：与设计 §5.0 实证同一手法——sync ``with`` 块内可含
    ``async for`` / ``await``，span 的 ``__enter__``/``__exit__`` 是同步的。
"""
from __future__ import annotations

import contextlib


def _get_tracer():
    """跨仓契约：取 EDPAgent 持有的 OTel tracer。

    返回 None 的情形：OTel 未启用、openjiuwen[observability] 未安装、或测试桩环境。
    任何导入/取数异常一律降级为 None（安全降级，不报错但无 trace——设计 §6 唯一失败模式）。
    """
    try:
        from agents.EDPAgent import get_otel_tracer

        return get_otel_tracer()
    except Exception:
        return None


def _set_attrs(span, attrs: dict) -> None:
    """把 attrs 中的非空项设到 span 上（空串/None 跳过，避免上报无意义空值）。"""
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    for key, value in (attrs or {}).items():
        if value is None or value == "":
            continue
        try:
            span.set_attribute(key, value)
        except Exception:
            # 属性类型不兼容等异常不阻断主流程
            pass


def set_span_attrs(span, attrs: dict) -> None:
    """供调用方在拿到结果后、``with`` 退出前补设 response 属性（公共出口）。

    tracer 关闭时 span 为 None → no-op；非 recording span 跳过。空串/None 值跳过。
    """
    _set_attrs(span, attrs)


def set_current_span_attrs(attrs: dict) -> None:
    """在当前活跃 span 上补属性（供 _call_versatile_adapter 等 finally 块使用）。

    场景：span 由调用方 ``with`` 创建并设为 current，被包方法内部拿不到 span 对象，
    但可以用 ``get_current_span()`` 取到它（与 v2.0 §4.2.4 ``_emit_metric`` 同手法）。
    无当前 span / OTel 未装 / 非 recording → no-op。
    """
    try:
        from opentelemetry.trace import get_current_span

        _set_attrs(get_current_span(), attrs)
    except ImportError:
        return


@contextlib.contextmanager
def _span(name: str, kind_str: str, session_id: str, attrs: dict | None = None):
    """统一 span context manager：tracer 为 None 时降级为空操作。

    ``kind_str`` ∈ {"server", "client"} → 映射 ``SpanKind.SERVER`` / ``CLIENT``；
    其余兜底 ``INTERNAL``。``session.id`` 始终设置（编排层会话根 key）；``attrs`` 中
    的非空 start 属性在进入时一并设置。response 属性由调用方在 yield 的 span 上补设。
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return
    from opentelemetry.trace import SpanKind

    kind = {
        "server": SpanKind.SERVER,
        "client": SpanKind.CLIENT,
    }.get(kind_str, SpanKind.INTERNAL)
    with tracer.start_as_current_span(name, kind=kind) as sp:
        _set_attrs(sp, {"session.id": session_id or "", **(attrs or {})})
        yield sp


def start_http_request_span(
    session_id: str,
    method: str = "",
    route: str = "",
    trace_id: str = "",
    agent_id: str = "",
):
    """``http.request`` [SERVER] —— 整条 trace 的根（v2.0 §3.2）。

    在 ``api/dispatch.py`` 的 ``TAG_HTTP_REQUEST_START`` 处调用；``http.response.status_code``
    等响应属性在 ``TAG_HTTP_REQUEST_END`` 处由调用方经 yield 的 span 补设。
    """
    attrs = {
        "http.request.method": method,
        "http.route": route,
    }
    if trace_id:
        attrs["openjiuwen.trace.id"] = trace_id
    if agent_id:
        attrs["openjiuwen.agent.name"] = agent_id
    return _span("http.request", "server", session_id, attrs)


def start_versatile_adapter_span(
    session_id: str,
    query_intent: str = "",
    query_description: str = "",
    dispatch_mode: str = "single",
    workflow_id: str = "",
    target_agent: str = "",
    sub_task_path: str = "",
):
    """``service.versatile_adapter`` [CLIENT] —— VA 出站调用（v2.0 §3.2/3.3）。

    单次调用（``_call_versatile_adapter`` / ``_continue_versatile_adapter``）用
    ``dispatch_mode="single"``；工作流并行调度（``_drive_workflow_va``）用
    ``dispatch_mode="parallel"`` 并带 workflow_id/target_agent/sub_task_path。
    response 属性（response_summary/cost/elapsed_ms/status/workflow_result）由调用方补设。
    """
    attrs = {
        "openjiuwen.va.dispatch_mode": dispatch_mode,
        "openjiuwen.va.query_intent": query_intent,
        "openjiuwen.va.query_description": query_description,
    }
    if workflow_id:
        attrs["openjiuwen.va.workflow_id"] = workflow_id
    if target_agent:
        attrs["openjiuwen.va.target_agent"] = target_agent
    if sub_task_path:
        attrs["openjiuwen.va.sub_task_path"] = sub_task_path
    return _span("service.versatile_adapter", "client", session_id, attrs)


def start_sub_agent_dispatch_span(
    session_id: str,
    entity_id: str = "",
    entity_name: str = "",
    query: str = "",
    sub_agent_url: str = "",
    sub_task_path: str = "",
    context_id: str = "",
):
    """``sub_agent.dispatch`` [CLIENT] —— 子 Agent 派发（v2.0 §3.3）。

    在 ``_run_one_sub_agent`` 调 ``_drive_sub_agent`` 处调用；并行调度每个子 Agent
    独立一个 span。response 属性（child_task_id/status/elapsed_ms/content_summary）
    由调用方在拿到结果后经 yield 的 span 补设。
    """
    attrs = {
        "openjiuwen.subagent.entity_id": entity_id,
        "openjiuwen.subagent.entity_name": entity_name,
        "openjiuwen.subagent.query": query,
        "openjiuwen.subagent.sub_agent_url": sub_agent_url,
        "openjiuwen.subagent.sub_task_path": sub_task_path,
        "openjiuwen.subagent.context_id": context_id,
    }
    return _span("sub_agent.dispatch", "client", session_id, attrs)
