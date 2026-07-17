# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""回归守卫：http.request span 已移至 api/dispatch.py（v2.0 §4.3.1），executor.execute()
不再创建任何 OTel span。本测试注入假 tracer 调 execute()，断言它不建 span——
防止后续误把 span 重新加回 execute（那样会和 dispatch 的根 span 嵌套成两个 http.request）。
"""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from unittest.mock import AsyncMock

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue

import orchestrator.otel_spans as otel_spans
from tests.framework_parallel._helpers import make_executor, make_fake_tracer, patch_tracer


async def test_execute_creates_no_span(monkeypatch):
    """execute() 不创建任何 OTel span（http 根 span 在 dispatch）。"""
    tracer = make_fake_tracer()
    patch_tracer(monkeypatch, tracer)  # 即便 tracer 可用，execute 也不应建 span

    executor = make_executor()

    async def _dispatch_stub(_event, _handler_context):
        return None

    executor._route_dispatcher.dispatch = _dispatch_stub
    executor._state_manager.get_task = AsyncMock(return_value=None)

    context = RequestContext(
        call_context=ServerCallContext(),
        request=None,
        task_id="task-x",
        context_id="conv-x",
        task=None,
    )
    queue = EventQueue()
    try:
        await executor.execute(context, queue)
    finally:
        await queue.close()

    assert tracer.created == [], "execute() 不应创建任何 OTel span（根 span 在 dispatch）"
