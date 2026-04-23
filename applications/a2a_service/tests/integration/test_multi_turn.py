"""
Test C: 多轮对话 step_counter 复位 + 跨 turn 状态独立。

关键契约（来自 executor.py:109 的 `step_counter=[0]`）：
  - 每次 `execute()` 都创建一个全新的 `[0]` 列表
  - 这个 counter 在一轮对话内 cascade 递归时共享（Task B 已测）
  - 两轮对话之间的 counter 应独立（本测试）

同时验证：
  - 每轮有独立的 EventQueue / 帧序列
  - conversation_start + conversation_end 在每轮都出现
  - execute() 的默认参数值不会在调用间泄漏（Python mutable default 陷阱）
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Artifact,
    Part,
    TaskArtifactUpdateEvent,
)
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct, Value

from common.events import (
    ConversationEndEvent,
    ConversationStartEvent,
    ThinkStartEvent,
    ToolEndEvent,
    ToolStartEvent,
    ToolStatusEvent,
)
from orchestrator.executor import Executor
from orchestrator.user_router import _serialize_event


CONV_ID = "conv-multi-turn"


# ════════════════════════════════════════════════════════════════════
# 辅助
# ════════════════════════════════════════════════════════════════════


def _make_executor() -> Executor:
    va_client = MagicMock()
    redis = MagicMock()
    redis.get_json = AsyncMock(return_value={})
    task_store = MagicMock()
    task_store.get = AsyncMock(return_value=None)
    task_store.save = AsyncMock()
    return Executor(va_client=va_client, redis=redis, task_store=task_store)


def _drain_queue(queue: EventQueue) -> list:
    inner = getattr(queue, "_queue", None) or getattr(queue, "queue", None)
    if inner is None:
        return []
    events = []
    try:
        while True:
            events.append(inner.get_nowait())
    except Exception:
        pass
    return events


def _extract_planning_contents(queue_events: list) -> list[str]:
    """从 event_queue 里抽出 planning_execution_process 的 content。"""
    contents: list[str] = []
    for ev in queue_events:
        if isinstance(ev, TaskArtifactUpdateEvent):
            for part in ev.artifact.parts:
                if part.WhichOneof("content") == "data":
                    data = MessageToDict(part.data)
                    if data.get("type") == "planning_execution_process":
                        contents.append(data.get("content", ""))
    return contents


# ════════════════════════════════════════════════════════════════════
# 测试 1：连续两次 _run_agent 调用，step_counter 各自从 1 开始
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_each_run_agent_invocation_starts_step_counter_from_one(monkeypatch):
    executor = _make_executor()

    async def fake_agent_stream(**kwargs):
        yield ConversationStartEvent()
        yield ToolStartEvent(content="工具 A", plugin="tool_a")
        yield ToolStatusEvent(content="工具 A", plugin="tool_a")
        yield ToolEndEvent(content="完成 A", plugin="tool_a")
        yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    # 第一轮
    queue1 = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id="t1", call_context=MagicMock(),
        query="第一轮", original_body={}, event_queue=queue1,
        cascade_result=None,
    )
    planning1 = _extract_planning_contents(_drain_queue(queue1))

    # 第二轮（新的 queue + 新的 _run_agent 调用）
    queue2 = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id="t2", call_context=MagicMock(),
        query="第二轮", original_body={}, event_queue=queue2,
        cascade_result=None,
    )
    planning2 = _extract_planning_contents(_drain_queue(queue2))

    # 两轮的 planning 都以 "步骤1" 开头
    assert len(planning1) == 1
    assert len(planning2) == 1
    assert "步骤1" in planning1[0]
    assert "步骤1" in planning2[0]


# ════════════════════════════════════════════════════════════════════
# 测试 2：一轮内 cascade 多次调用，step_counter 持续累加（回归保护）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step_counter_continues_within_single_run_agent(monkeypatch):
    """同一次 _run_agent 内的多 tool 应延续计数（Task B 已验，这里做回归保护）。"""
    executor = _make_executor()

    async def fake_agent_stream(**kwargs):
        yield ToolStartEvent(content="A", plugin="a")
        yield ToolStatusEvent(content="A", plugin="a")
        yield ToolEndEvent(content="done A", plugin="a")
        yield ToolStartEvent(content="B", plugin="b")
        yield ToolStatusEvent(content="B", plugin="b")
        yield ToolEndEvent(content="done B", plugin="b")
        yield ToolStartEvent(content="C", plugin="c")
        yield ToolStatusEvent(content="C", plugin="c")
        yield ToolEndEvent(content="done C", plugin="c")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id="t", call_context=MagicMock(),
        query="x", original_body={}, event_queue=queue, cascade_result=None,
    )
    planning = _extract_planning_contents(_drain_queue(queue))
    assert len(planning) == 3
    assert "步骤1" in planning[0]
    assert "步骤2" in planning[1]
    assert "步骤3" in planning[2]


# ════════════════════════════════════════════════════════════════════
# 测试 3：mutable default 陷阱防御 —— 默认参数不会在调用间泄漏
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_default_step_counter_does_not_leak_across_calls(monkeypatch):
    """Python 经典陷阱：def f(x=[]): ... 会让 [] 在调用间共享。
    我们的实现用 step_counter=None + if None: step_counter=[0]，应防御住这个坑。
    """
    executor = _make_executor()

    async def fake_agent_stream(**kwargs):
        yield ToolStartEvent(content="X", plugin="x")
        yield ToolStatusEvent(content="X", plugin="x")
        yield ToolEndEvent(content="done", plugin="x")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    # 连续跑 5 次，每次都不传 step_counter
    for i in range(5):
        queue = EventQueue()
        await executor._run_agent(
            conv_id=CONV_ID, task_id=f"t{i}", call_context=MagicMock(),
            query="x", original_body={}, event_queue=queue, cascade_result=None,
            # 注意：不显式传 step_counter，用默认值
        )
        planning = _extract_planning_contents(_drain_queue(queue))
        assert len(planning) == 1, f"第 {i+1} 次调用应有 1 个 planning 帧"
        assert "步骤1" in planning[0], (
            f"第 {i+1} 次调用 step counter 应从 1 开始，"
            f"实际 content={planning[0]!r}"
        )


# ════════════════════════════════════════════════════════════════════
# 测试 4：每轮有独立的帧序列（跨轮帧不串）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_frames_are_isolated_per_turn(monkeypatch):
    """queue 各自独立 → 第一轮的帧不会混入第二轮。"""
    executor = _make_executor()

    call_count = [0]

    async def varying_stream(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield ConversationStartEvent(content="第一轮开始")
            yield ThinkStartEvent(content="思考中")
            yield ConversationEndEvent(content="第一轮结束")
        else:
            yield ConversationStartEvent(content="第二轮开始")
            yield ConversationEndEvent(content="第二轮结束")

    monkeypatch.setattr("orchestrator.executor.agent_stream", varying_stream)

    queue1 = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id="t1", call_context=MagicMock(),
        query="q1", original_body={}, event_queue=queue1, cascade_result=None,
    )
    events1 = _drain_queue(queue1)

    queue2 = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id="t2", call_context=MagicMock(),
        query="q2", original_body={}, event_queue=queue2, cascade_result=None,
    )
    events2 = _drain_queue(queue2)

    def contents_of(events):
        out = []
        for ev in events:
            if isinstance(ev, TaskArtifactUpdateEvent):
                for part in ev.artifact.parts:
                    if part.WhichOneof("content") == "data":
                        data = MessageToDict(part.data)
                        out.append(data.get("content", ""))
        return out

    contents1 = contents_of(events1)
    contents2 = contents_of(events2)

    # 第一轮含"第一轮"标识，不含"第二轮"
    assert any("第一轮" in c for c in contents1)
    assert not any("第二轮" in c for c in contents1)
    # 第二轮反之
    assert any("第二轮" in c for c in contents2)
    assert not any("第一轮" in c for c in contents2)


# ════════════════════════════════════════════════════════════════════
# 测试 5：每轮独立 serialize，execution_time 的 start_time 不串
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_serialize_start_time_is_per_turn(monkeypatch):
    """_serialize_event 的 start_time 由上层 generate() 决定；
    模拟"上层对每轮独立计时"，execution_time 应从接近 0 开始。"""
    import time

    executor = _make_executor()

    async def fake_agent_stream(**kwargs):
        yield ConversationStartEvent(content="start")
        yield ConversationEndEvent(content="end")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    # 第一轮
    queue1 = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id="t1", call_context=MagicMock(),
        query="q1", original_body={}, event_queue=queue1, cascade_result=None,
    )
    start1 = time.monotonic()
    times1 = []
    for ev in _drain_queue(queue1):
        payload = _serialize_event(
            ev, agent_id="a", conversation_id=CONV_ID, start_time=start1,
        )
        if payload:
            times1.append(json.loads(payload)["execution_time"])

    # 模拟两轮之间经过一段时间
    time.sleep(0.02)

    # 第二轮
    queue2 = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id="t2", call_context=MagicMock(),
        query="q2", original_body={}, event_queue=queue2, cascade_result=None,
    )
    start2 = time.monotonic()  # 新一轮的起点
    times2 = []
    for ev in _drain_queue(queue2):
        payload = _serialize_event(
            ev, agent_id="a", conversation_id=CONV_ID, start_time=start2,
        )
        if payload:
            times2.append(json.loads(payload)["execution_time"])

    # 两轮的 execution_time 都应接近 0（因为每轮都用新的 start_time）
    # 不能跨轮累加
    assert all(t < 0.5 for t in times1 if isinstance(t, (int, float)))
    assert all(t < 0.5 for t in times2 if isinstance(t, (int, float)))


# ════════════════════════════════════════════════════════════════════
# 测试 6：conversation_start 与 conversation_end 每轮各出现一次
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_each_turn_has_own_conversation_markers(monkeypatch):
    executor = _make_executor()

    async def fake_agent_stream(**kwargs):
        yield ConversationStartEvent()
        yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    for turn_idx in range(3):
        queue = EventQueue()
        await executor._run_agent(
            conv_id=CONV_ID, task_id=f"t{turn_idx}", call_context=MagicMock(),
            query="x", original_body={}, event_queue=queue, cascade_result=None,
        )
        types_seen = set()
        for ev in _drain_queue(queue):
            if isinstance(ev, TaskArtifactUpdateEvent):
                for part in ev.artifact.parts:
                    if part.WhichOneof("content") == "data":
                        data = MessageToDict(part.data)
                        types_seen.add(data.get("type"))
        assert "conversation_start" in types_seen
        assert "conversation_end" in types_seen
