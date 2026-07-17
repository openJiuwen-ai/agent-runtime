# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Stage 2 traceparent 跨 Agent 传播测试（v2.0 §4.3.4 inject / §4.3.5 extract）。

- inject：_drive_sub_agent 发出的 A2A Message session_context 含 traceparent + session_id
- extract/otel_session_id：execute() 从 session_context 取出并透传到 handler_context / turn_ctx
  （attach/detach 是标准 try/finally，不 mock opentelemetry 内部计数——改测可观察的透传 + smoke）

attach/detach 的配对由「execute 正常完成 / 异常均不泄漏 context」的 smoke 用例覆盖
（若 attach 后未 detach 会抛 Token 类型错或污染后续用例）。
"""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import Message, Part
from google.protobuf.json_format import MessageToDict

import orchestrator.handlers.remote_agent_handler as remote_module
from tests.framework_parallel._helpers import (
    FakeAsyncStream,
    FakeSubAgentClient,
    data_part,
    make_executor,
    make_turn_ctx,
    sr_completed_text,
    sr_task,
)

_SPEC = {"entity_id": "A", "entity_name": "EA", "query": "q", "url": ""}
# 合法格式的 traceparent（trace_id 32 hex / span_id 16 hex），避免 extract 解析告警
_TP = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"


# ── inject：主 Agent 侧把 traceparent + session_id 写入 A2A Message ──────────


async def test_drive_sub_agent_injects_traceparent_and_session_id(monkeypatch):
    """_drive_sub_agent 调 inject() 并把结果 + 原始 conversation_id 放进 session_context。"""
    import opentelemetry.propagate as otprop

    monkeypatch.setattr(otprop, "inject", lambda carrier: carrier.update({"traceparent": "00-fake-trace-01"}))

    client = FakeSubAgentClient(send=[FakeAsyncStream([sr_task("child-1"), sr_completed_text("done")])])
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(conv_id="conv-main")

    await handler._drive_sub_agent(
        remote_module._DriveSubAgentRequest(
            client=client, spec=_SPEC, query="q", turn_ctx=ctx, path=["A"],
        )
    )

    sent = client.send_requests[0]
    data_part_msg = next(p for p in sent.message.parts if p.WhichOneof("content") == "data")
    session_context = MessageToDict(data_part_msg.data)["session_context"]
    assert session_context["traceparent"] == "00-fake-trace-01", "traceparent 经 inject 写入"
    assert session_context["session_id"] == "conv-main", "session_id = 主 Agent 原始 conversation_id"
    assert "sub_task_path" in session_context and "headers" in session_context  # 原有字段不丢


# ── extract：子 Agent 侧 execute() 透传 otel_session_id（attach/detach 走真路径）──


def _context_with_traceparent(traceparent: str, session_id: str, conv_id: str = "conv-sub-A"):
    msg = Message(
        role=1,
        message_id="m",
        parts=[
            Part(text="hi"),
            data_part({"session_context": {"traceparent": traceparent, "session_id": session_id}}),
        ],
    )
    return SimpleNamespace(
        context_id=conv_id, task_id="t", call_context=None, current_task=None, message=msg,
    )


def _stub_dispatch(executor, record: dict):
    async def _dispatch_stub(_event, handler_context):
        record["otel_session_id"] = handler_context.get("otel_session_id")
        record["turn_otel_session_id"] = getattr(handler_context.get("turn_ctx"), "otel_session_id", "<missing>")
        record["conv_id"] = handler_context.get("conv_id")
        return None

    executor._route_dispatcher.dispatch = _dispatch_stub
    executor._state_manager.get_task = AsyncMock(return_value=None)


async def test_execute_propagates_otel_session_id_when_traceparent_present():
    """有 traceparent：otel_session_id 透传到 handler_context + turn_ctx；conv_id 不被覆盖。"""
    executor = make_executor()
    record: dict = {}
    _stub_dispatch(executor, record)

    queue = EventQueue()
    await executor.execute(_context_with_traceparent(_TP, "orig-conv"), queue)
    await queue.close()

    assert record["otel_session_id"] == "orig-conv"
    assert record["turn_otel_session_id"] == "orig-conv"
    assert record["conv_id"] == "conv-sub-A", "conv_id 保持 context_id 不变（业务用，不被覆盖）"


async def test_execute_otel_session_id_empty_when_no_traceparent():
    """无 traceparent（主 Agent 场景）：otel_session_id 为空，conv_id 照常。"""
    executor = make_executor()
    record: dict = {}
    _stub_dispatch(executor, record)

    msg = Message(role=1, message_id="m", parts=[Part(text="hi")])
    ctx = SimpleNamespace(
        context_id="conv-main", task_id="t", call_context=None, current_task=None, message=msg,
    )
    queue = EventQueue()
    await executor.execute(ctx, queue)
    await queue.close()

    assert record["otel_session_id"] in ("", None)
    assert record["conv_id"] == "conv-main"


async def test_execute_runs_clean_with_traceparent_no_context_leak():
    """有 traceparent：execute 正常完成（attach 后 detach 配对，不污染 OTel context）。

    若 detach 未配对，会污染 opentelemetry 全局 context，影响后续——这里跑两轮验证无泄漏。
    """
    executor = make_executor()
    record: dict = {}
    _stub_dispatch(executor, record)

    for _ in range(2):
        queue = EventQueue()
        await executor.execute(_context_with_traceparent(_TP, "orig-conv"), queue)
        await queue.close()
    assert record["otel_session_id"] == "orig-conv"  # 两轮都正常


async def test_execute_completes_when_dispatch_raises_with_traceparent():
    """dispatch 抛异常时，execute 仍 detach（finally 配对），异常正常向上抛。"""
    executor = make_executor()
    executor._state_manager.get_task = AsyncMock(return_value=None)

    async def _boom(_event, _handler_context):
        raise RuntimeError("dispatch failed")

    executor._route_dispatcher.dispatch = _boom

    queue = EventQueue()
    raised = False
    try:
        await executor.execute(_context_with_traceparent(_TP, "orig"), queue)
    except RuntimeError:
        raised = True
    finally:
        await queue.close()
    assert raised, "异常应向上抛"
