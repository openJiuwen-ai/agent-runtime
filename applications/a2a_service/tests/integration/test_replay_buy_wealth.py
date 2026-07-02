# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Phase 5 回放集成测试：模拟"买理财"抓包的核心序列，端到端验证输出。

链路：mock agent_stream → Executor.run_agent → EventQueue → _serialize_event
      → 拿到完整 SSE 帧列表 → 逐帧断言

这个测试覆盖 Phase 1-4 的所有关键修改：
  - Pattern A 包装器（Phase 1）
  - _serialize_event 调度（Phase 2）
  - planning_execution_process 自动发射（Phase 3）
  - SummaryEvent 流式 + FinalAnswerChunkEvent 全量（Phase 4）
  - todolist_item HTML <br/> 格式（Phase 4）
  - tool_status 跟发 tool_start（Phase 3）
  - todo_start 首次 in_progress 触发（Phase 3）
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events import EventQueue
from google.protobuf.json_format import MessageToDict

from agents.EDPAgent.events import (
    ConversationEndEvent,
    ConversationStartEvent,
    FinalAnswerChunkEvent,
    FinalAnswerEndEvent,
    FinalAnswerStartEvent,
    SummaryEvent,
    ThinkChunkEvent,
    ThinkEndEvent,
    ThinkStartEvent,
    ToolEndEvent,
    ToolStartEvent,
    ToolStatusEvent,
    TodoListEndEvent,
    TodoListItemEvent,
    TodoListStartEvent,
    TodoStartEvent,
    TodoStatusEvent,
)
from orchestrator.executor import Executor, _TurnContext
from api.dispatch import _serialize_event


AGENT_ID = "fcbcd0ce-73b0-4097-a0cb-6286341f88f6"
CONV_ID = "90d40c85-cca4-43fe-8e3f-9ad3717fb1b4"


def _make_executor() -> Executor:
    va_client = MagicMock()
    redis = MagicMock()
    task_store = MagicMock()
    task_store.get = AsyncMock(return_value=None)
    task_store.save = AsyncMock()
    return Executor(va_client=va_client, redis=redis, task_store=task_store)


def _make_turn_ctx(queue: EventQueue, *, task_id: str = "t") -> _TurnContext:
    return _TurnContext(
        conv_id=CONV_ID,
        task_id=task_id,
        call_context=MagicMock(),
        event_queue=queue,
    )


def _drain_queue(queue: EventQueue) -> list:
    """从 EventQueue 内部抽出所有 enqueue 过的 event。"""
    inner = getattr(queue, "_queue", None) or getattr(queue, "queue", None)
    if inner is None:
        return []
    events = []
    try:
        while True:
            events.append(inner.get_nowait())
    except asyncio.QueueEmpty:
        return events
    return events


def _collect_serialized_frames(queue: EventQueue) -> list[dict]:
    """对每个 protobuf event 过 _serialize_event，转成 wrapped dict 列表。"""
    frames: list[dict] = []
    for ev in _drain_queue(queue):
        payload = _serialize_event(
            ev,
            agent_id=AGENT_ID,
            conversation_id=CONV_ID,
            start_time=0.0,
        )
        if payload is None:
            continue
        frames.append(json.loads(payload))
    return frames


# ════════════════════════════════════════════════════════════════════
# 场景 1：最简完整链路（conversation → think → todolist → tool → answer）
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def simple_buy_wealth_stream(monkeypatch):
    """把 agent_stream 替换成一段"买理财"最简序列。"""

    async def fake_stream(**kwargs):
        # 对话开始
        yield ConversationStartEvent(content="本轮对话开始")

        # 思考
        yield ThinkStartEvent(content="准备进行步骤规划")
        yield ThinkChunkEvent(content="我来")
        yield ThinkChunkEvent(content="帮您购买理财产品")
        yield ThinkEndEvent(content="本次规划结束")

        # 任务清单（含 HTML 格式 content）
        yield TodoListStartEvent(content="规划任务清单")
        yield TodoListItemEvent(
            id=1, title="推荐理财产品", status="pending",
            content="1.推荐理财产品（待执行）<br/>",
        )
        yield TodoListItemEvent(
            id=2, title="购买理财产品", status="pending",
            content="2.购买理财产品（未完成）<br/>",
        )
        yield TodoListEndEvent(content="任务清单规划完成")

        # 单任务开始
        yield TodoStartEvent(id=1, title="推荐理财产品", content="推荐理财产品")
        yield TodoStatusEvent(id=1, status="in_progress", content="正在推荐理财产品")

        # 工具调用（Executor 会在之前自动发 planning_execution_process）
        # 注：_StreamProcessor 真实运行时会自动在 ToolStartEvent 后补 ToolStatusEvent；
        # 这里 mock 直接走 agent_stream 层，需要显式 yield 以匹配约定
        yield ToolStartEvent(
            content="开始调用推荐工具",
            plugin="product_recommend_skill",
            args={"task": "推荐理财产品"},
        )
        yield ToolStatusEvent(
            content="开始调用推荐工具",
            plugin="product_recommend_skill",
        )
        yield ToolEndEvent(
            content="已完成理财产品推荐",
            plugin="product_recommend_skill",
            data={"status": "done"},
        )

        # 总结流式 + 全量（Phase 4 规范）
        yield FinalAnswerStartEvent(content="开始进行总结")
        yield SummaryEvent(content="已")
        yield SummaryEvent(content="为您完成")
        yield SummaryEvent(content="如下事项：")
        yield SummaryEvent(content="\n1. 理财产品推荐（完成）")
        yield FinalAnswerChunkEvent(
            content="已为您完成如下事项：\n1. 理财产品推荐（完成）"
        )
        yield FinalAnswerEndEvent(content="总结结束")

        # 对话结束
        yield ConversationEndEvent(content="本轮对话结束")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_stream)


@pytest.mark.asyncio
async def test_replay_simple_buy_wealth_end_to_end(simple_buy_wealth_stream):
    """完整回放：验证总帧数、事件顺序、关键字段。"""
    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue, task_id="task-1"),
        query="我要买理财",
        original_body={},
        cascade_result=None,
    )

    frames = _collect_serialized_frames(queue)

    # 提取事件序列（注：ToolEndEvent 已和 FinalAnswerEndEvent 合并为 TaskStatusUpdateEvent 路径）
    event_sequence = [f["custom_rsp_data"]["event"] for f in frames]

    # 第一帧必须是 conversation_start
    assert event_sequence[0] == "conversation_start"

    # planning_execution_process 应在 tool_start 之前
    pep_idx = event_sequence.index("planning_execution_process")
    tool_start_idx = event_sequence.index("tool_start")
    assert pep_idx < tool_start_idx

    # tool_status 跟在 tool_start 之后
    tool_status_idx = event_sequence.index("tool_status")
    assert tool_status_idx == tool_start_idx + 1

    # summary 是 spec 外但实现透传的事件（与 AgentEngine 对齐，见 D-1），
    # 4 条流式 summary + 1 条 final_answer_chunk 全量
    summary_count = event_sequence.count("summary")
    chunk_count = event_sequence.count("final_answer_chunk")
    assert summary_count == 4  # 4 个流式片段
    assert chunk_count == 1    # 1 条全量


@pytest.mark.asyncio
async def test_all_frames_are_pattern_a(simple_buy_wealth_stream):
    """这个场景无 Versatile 调用，所有帧都应是 Pattern A（含 output/error）。"""
    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)
    for f in frames:
        assert f["success"] is True
        assert f["agent_id"] == AGENT_ID
        assert f["conversation_id"] == CONV_ID
        assert "output" in f
        assert "error" in f
        assert "custom_rsp_data" in f


@pytest.mark.asyncio
async def test_all_frames_have_numeric_execution_time(simple_buy_wealth_stream):
    """对齐 spec §2.3.3：所有 agent event 的 execution_time 都是数字（含 think_chunk）。"""
    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)

    for f in frames:
        event_type = f["custom_rsp_data"]["event"]
        exec_time = f["execution_time"]
        assert isinstance(exec_time, (int, float)), (
            f"{event_type} 的 execution_time 应是数字，实际 {exec_time!r}"
        )


@pytest.mark.asyncio
async def test_planning_execution_process_has_error_code(simple_buy_wealth_stream):
    """决策 2 抓包对齐：planning_execution_process 独有 error_code 字段。"""
    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)

    for f in frames:
        event_type = f["custom_rsp_data"]["event"]
        if event_type == "planning_execution_process":
            assert "error_code" in f
            assert f["error_code"] == ""
        else:
            assert "error_code" not in f, (
                f"{event_type} 不应有 error_code"
            )


@pytest.mark.asyncio
async def test_todolist_item_content_has_html_br(simple_buy_wealth_stream):
    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)
    for f in frames:
        if f["custom_rsp_data"]["event"] == "todolist_item":
            assert f["custom_rsp_data"]["content"].endswith("<br/>")


@pytest.mark.asyncio
async def test_latency_and_plugin_always_empty_string(simple_buy_wealth_stream):
    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)
    tool_events = {"tool_start", "tool_status", "tool_end"}
    for f in frames:
        inner = f["custom_rsp_data"]
        assert inner["latency"] == ""
        # plugin 只有 tool_* 事件才带工具名，其余事件空串
        if inner["event"] in tool_events:
            assert inner["plugin"], f"tool event 应该带 plugin，实际为空: {inner}"
        else:
            assert inner["plugin"] == ""


@pytest.mark.asyncio
async def test_planning_step_counter_matches_tool_count(simple_buy_wealth_stream):
    """单一 ToolStartEvent 应仅触发 1 条 planning_execution_process(步骤 1)。"""
    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)

    planning_frames = [
        f for f in frames
        if f["custom_rsp_data"]["event"] == "planning_execution_process"
    ]
    tool_start_frames = [
        f for f in frames
        if f["custom_rsp_data"]["event"] == "tool_start"
    ]
    assert len(planning_frames) == len(tool_start_frames) == 1
    assert "步骤1" in planning_frames[0]["custom_rsp_data"]["content"]
    assert "(tool=product_recommend_skill)" in planning_frames[0]["custom_rsp_data"]["content"]


# ════════════════════════════════════════════════════════════════════
# 场景 2：多步骤链路（2 个 tool 调用）—— 验证 step_counter 递增
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_multi_tool_step_counter_monotonic(monkeypatch):
    async def multi_tool_stream(**kwargs):
        yield ConversationStartEvent()
        yield ToolStartEvent(content="第一步", plugin="tool_a")
        yield ToolEndEvent(content="完成第一步", plugin="tool_a")
        yield ToolStartEvent(content="第二步", plugin="tool_b")
        yield ToolEndEvent(content="完成第二步", plugin="tool_b")
        yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", multi_tool_stream)

    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)

    planning_contents = [
        f["custom_rsp_data"]["content"]
        for f in frames
        if f["custom_rsp_data"]["event"] == "planning_execution_process"
    ]
    assert len(planning_contents) == 2
    assert "步骤1" in planning_contents[0]
    assert "(tool=tool_a)" in planning_contents[0]
    assert "步骤2" in planning_contents[1]
    assert "(tool=tool_b)" in planning_contents[1]


# ════════════════════════════════════════════════════════════════════
# 场景 3：summary 拼接 = final_answer_chunk（Phase 4 规范）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_summary_stream_equals_final_answer_chunk(monkeypatch):
    """流式 summary 片段拼接后应与 final_answer_chunk 一致
    （summary 不屏蔽，与 AgentEngine 对齐，见 D-1）。
    """
    pieces = ["已", "为您完成", "如下事项"]
    full_text = "".join(pieces)

    async def summary_stream(**kwargs):
        yield ConversationStartEvent()
        yield FinalAnswerStartEvent()
        for p in pieces:
            yield SummaryEvent(content=p)
        yield FinalAnswerChunkEvent(content=full_text)
        yield FinalAnswerEndEvent(content=full_text)
        yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", summary_stream)

    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)

    summary_frames = [
        f for f in frames if f["custom_rsp_data"]["event"] == "summary"
    ]
    chunk_frames = [
        f for f in frames if f["custom_rsp_data"]["event"] == "final_answer_chunk"
    ]

    assert len(summary_frames) == 3
    assert len(chunk_frames) == 1
    # 流式拼接 = 全量
    concatenated = "".join(f["custom_rsp_data"]["content"] for f in summary_frames)
    assert concatenated == chunk_frames[0]["custom_rsp_data"]["content"] == full_text


# ════════════════════════════════════════════════════════════════════
# 场景 4：execution_time 单调递增
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_execution_time_monotonic_across_frames(simple_buy_wealth_stream):
    """所有 agent event 的 execution_time 应单调递增（都已是数字）。"""
    import time

    executor = _make_executor()
    queue = EventQueue()

    # 跑一遍后 frames 已产生；但 _serialize_event 的 start_time 是参数
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    # 用真实 monotonic 序列化
    start = time.monotonic()
    times_seq: list[float] = []
    for ev in _drain_queue(queue):
        payload = _serialize_event(
            ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=start,
        )
        if payload is None:
            continue
        f = json.loads(payload)
        exec_t = f["execution_time"]
        if isinstance(exec_t, (int, float)):
            times_seq.append(exec_t)
        time.sleep(0.001)  # 保证连续帧 execution_time 可辨

    # 单调非递减
    for a, b in zip(times_seq, times_seq[1:]):
        assert b >= a, f"execution_time 不应递减：{a} → {b}"


# ════════════════════════════════════════════════════════════════════
# 场景 5：无业务工具（超业务范围场景）—— 只产最小帧序
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_out_of_scope_scenario_emits_minimal_frames(monkeypatch):
    """场景 11: "我要赚i豆" → 不调工具，只有 conversation + think + final。"""

    async def oos_stream(**kwargs):
        yield ConversationStartEvent()
        yield ThinkStartEvent()
        yield ThinkChunkEvent(content="用户请求超出范围")
        yield ThinkEndEvent()
        yield FinalAnswerStartEvent()
        yield SummaryEvent(content="尚在学习中")
        yield FinalAnswerChunkEvent(content="尚在学习中")
        yield FinalAnswerEndEvent(content="尚在学习中")
        yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", oos_stream)

    executor = _make_executor()
    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="我要赚i豆",
        original_body={},
        cascade_result=None,
    )
    frames = _collect_serialized_frames(queue)
    event_seq = [f["custom_rsp_data"]["event"] for f in frames]

    # 无 planning_execution_process / tool_* 帧
    assert "planning_execution_process" not in event_seq
    assert "tool_start" not in event_seq
    assert "tool_end" not in event_seq
    # 仍然有完整的对话-思考-总结闭环
    assert "conversation_start" in event_seq
    assert "think_start" in event_seq
    assert "final_answer_end" in event_seq
    assert "conversation_end" in event_seq  # spec 外事件，透传（与 AgentEngine 对齐）
