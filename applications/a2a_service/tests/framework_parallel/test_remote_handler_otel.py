# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""RemoteAgentHandler 的 VA / sub_agent span 测试。

span 接在调用点（_run_one_workflow → _drive_workflow_va；_run_one_sub_agent → _drive_sub_agent），
故经这两个外层方法驱动以覆盖真实接线。sub_agent 用 FakeSubAgentClient 跑真 _drive_sub_agent；
VA 用打桩 _drive_workflow_va 避开 VA 帧解析，聚焦 span 名/种类。
"""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from opentelemetry.trace import SpanKind

from tests.framework_parallel._helpers import (
    FakeAsyncStream,
    FakeSubAgentClient,
    make_executor,
    make_fake_tracer,
    make_turn_ctx,
    patch_tracer,
    sr_completed_text,
    sr_task,
)

_SPEC = {"entity_id": "A", "entity_name": "Entity A", "query": "Analyze A", "url": ""}


# ── sub_agent.dispatch span ────────────────────────────────────────────────


async def test_sub_agent_dispatch_creates_one_span(monkeypatch):
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    client = FakeSubAgentClient(
        send=[FakeAsyncStream([sr_task("child-1"), sr_completed_text("done")])]
    )
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler

    result = await handler._run_one_sub_agent(_SPEC, make_turn_ctx(conv_id="conv-sub"))

    assert result["status"] == "done"
    assert len(tracer.created) == 1, "单子 Agent 派发 = 1 个 sub_agent.dispatch span"
    args, kwargs, _span = tracer.created[0]
    assert args == ("sub_agent.dispatch",)
    assert kwargs == {"kind": SpanKind.CLIENT}


async def test_sub_agent_runs_normally_when_disabled(monkeypatch):
    """OTel 关闭：子 Agent 派发照常完成，不创建 span（回归零侵入）。"""
    patch_tracer(monkeypatch, None)
    client = FakeSubAgentClient(
        send=[FakeAsyncStream([sr_task("child-1"), sr_completed_text("done")])]
    )
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler

    result = await handler._run_one_sub_agent(_SPEC, make_turn_ctx(conv_id="conv-sub-off"))

    assert result["status"] == "done"


# ── service.versatile_adapter span ─────────────────────────────────────────


async def test_workflow_va_call_creates_one_span(monkeypatch):
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)
    executor = make_executor()
    handler = executor._test_remote_handler

    async def _fake_drive(turn_ctx, delegate, path, cancel_event):  # noqa: ARG001
        return {"workflow_result": "ok"}

    handler._drive_workflow_va = _fake_drive  # 打桩内层 VA 驱动，聚焦 span 接线

    result = await handler._run_one_workflow(
        {"workflow_id": "wf1", "intent": "x"}, make_turn_ctx(conv_id="conv-va")
    )

    assert result["status"] == "done"
    assert len(tracer.created) == 1, "单工作流 VA 调用 = 1 个 versatile_adapter span"
    args, kwargs, _span = tracer.created[0]
    assert args == ("service.versatile_adapter",)
    assert kwargs == {"kind": SpanKind.CLIENT}
