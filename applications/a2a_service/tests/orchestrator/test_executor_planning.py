# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Executor 的 planning_execution_process 发射集成测试（Phase 3.3）。

通过 monkeypatch `agent_stream` 生成一段受控的事件序列，观察 Executor 是否
在每个 ToolStartEvent / DelegateRequest 之前发射 planning_execution_process，
且 step_counter 在 cascade 续轮时继续累加。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events import EventQueue
from google.protobuf.json_format import MessageToDict

from common.events import (
    ConversationStartEvent,
    ConversationEndEvent,
    DelegateRequest,
    ThinkStartEvent,
    ThinkEndEvent,
    ToolStartEvent,
    ToolEndEvent,
)
from orchestrator.executor import Executor, _TurnContext


def _collect_emitted_events(event_queue: EventQueue) -> list:
    """从 EventQueue 内部缓冲把 enqueue 过的 event 抽出来（不消费顺序信号）。"""
    events = []
    # a2a EventQueue 使用 asyncio.Queue 作为内部，这里用内省的方式读取
    inner = getattr(event_queue, "_queue", None) or getattr(event_queue, "queue", None)
    if inner is None:
        return events
    try:
        while True:
            events.append(inner.get_nowait())
    except asyncio.QueueEmpty:
        return events
    return events


def _event_type_of(a2a_event) -> str | None:
    """从 TaskArtifactUpdateEvent 里抽出 AgentEvent 的 type 字符串。"""
    artifact = getattr(a2a_event, "artifact", None)
    if artifact is None:
        return None
    for part in artifact.parts:
        if part.WhichOneof("content") == "data":
            data = MessageToDict(part.data)
            if isinstance(data, dict) and "type" in data:
                return data["type"]
    return None


def _make_executor() -> Executor:
    """构造一个 Executor，用 MagicMock 填充所有外部依赖。"""
    va_client = MagicMock()
    redis = MagicMock()
    task_store = MagicMock()
    task_store.get = AsyncMock(return_value=None)
    task_store.save = AsyncMock()
    return Executor(va_client=va_client, redis=redis, task_store=task_store)


@pytest.mark.asyncio
async def test_planning_event_emitted_before_each_tool_start(monkeypatch):
    """每次 ToolStartEvent 前都应有一条 planning_execution_process。"""
    executor = _make_executor()

    async def fake_agent_stream(**kwargs):
        yield ConversationStartEvent()
        yield ThinkStartEvent()
        yield ThinkEndEvent()
        yield ToolStartEvent(content="查询余额", plugin="query_balance")
        yield ToolEndEvent(content="余额 1000", plugin="query_balance")
        yield ToolStartEvent(content="转账", plugin="transfer")
        yield ToolEndEvent(content="转账完成", plugin="transfer")
        yield ConversationEndEvent()

    monkeypatch.setattr(
        "orchestrator.executor.agent_stream", fake_agent_stream,
    )

    event_queue = EventQueue()
    turn_ctx = _TurnContext(
        conv_id="conv-1",
        task_id="task-1",
        call_context=MagicMock(),
        event_queue=event_queue,
    )
    await executor.run_agent(
        turn_ctx,
        query="测试",
        original_body={},
        cascade_result=None,
    )

    emitted = _collect_emitted_events(event_queue)
    types = [_event_type_of(e) for e in emitted]

    # 应出现 2 次 planning_execution_process（2 个 tool_start）
    planning_indices = [i for i, t in enumerate(types) if t == "planning_execution_process"]
    tool_start_indices = [i for i, t in enumerate(types) if t == "tool_start"]
    assert len(planning_indices) == 2
    assert len(tool_start_indices) == 2
    # 每次 planning 必须紧挨在对应 tool_start 之前
    for p_idx, t_idx in zip(planning_indices, tool_start_indices):
        assert p_idx < t_idx, (
            f"planning (at {p_idx}) 应在 tool_start (at {t_idx}) 之前"
        )


@pytest.mark.asyncio
async def test_step_counter_increments_with_each_tool_start(monkeypatch):
    """连续两次 ToolStart 的 planning content 应含 "步骤1" 和 "步骤2"。"""
    executor = _make_executor()

    async def fake_agent_stream(**kwargs):
        yield ToolStartEvent(content="A", plugin="tool_a")
        yield ToolEndEvent(content="done A", plugin="tool_a")
        yield ToolStartEvent(content="B", plugin="tool_b")
        yield ToolEndEvent(content="done B", plugin="tool_b")

    monkeypatch.setattr(
        "orchestrator.executor.agent_stream", fake_agent_stream,
    )

    event_queue = EventQueue()
    turn_ctx = _TurnContext(
        conv_id="conv-1",
        task_id="task-1",
        call_context=MagicMock(),
        event_queue=event_queue,
    )
    await executor.run_agent(
        turn_ctx,
        query="x",
        original_body={},
        cascade_result=None,
    )

    planning_contents = []
    for ev in _collect_emitted_events(event_queue):
        if _event_type_of(ev) != "planning_execution_process":
            continue
        for part in ev.artifact.parts:
            if part.WhichOneof("content") == "text":
                planning_contents.append(part.text)
                break

    assert len(planning_contents) == 2
    assert "步骤1" in planning_contents[0]
    assert "(tool=tool_a)" in planning_contents[0]
    assert "步骤2" in planning_contents[1]
    assert "(tool=tool_b)" in planning_contents[1]
