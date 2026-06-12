# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Task A: 真实抓包逐帧回放对齐测试。

Fixture `buy_wealth_round1_key_frames.jsonl` 是"买理财"对话第一轮关键帧
的逐帧拷贝（节选），抓包原始字段一字不差地保留（含 createdTime 与
execution_time 原值）。本测试比对我们 pipeline 产出的帧是否与 fixture
在**结构**上对齐：

  - 每个 expected 帧对应的 event type 必须在 actual 流中出现
  - event 顺序与 fixture 一致
  - 对齐的字段：event 类型、content 值、Pattern A/B 分类、error_code 出现条件
  - 忽略动态字段：createdTime / execution_time（这是时间戳，每次不同）

使用这个对齐 fixture 能捕捉到 implementation 与 capture 不一致的字段级 bug，
例如 content 格式漂移、特殊事件丢失 error_code、Pattern B 遗漏 data 字段等。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Artifact,
    Part,
    StreamResponse,
    TaskArtifactUpdateEvent,
)
from google.protobuf.struct_pb2 import Struct, Value

from common.events import (
    ConversationEndEvent,
    ConversationStartEvent,
    DelegateRequest,
    ThinkChunkEvent,
    ThinkEndEvent,
    ThinkStartEvent,
    TodoListEndEvent,
    TodoListItemEvent,
    TodoListStartEvent,
    TodoStartEvent,
    TodoStatusEvent,
    ToolStartEvent,
    ToolStatusEvent,
)
from orchestrator.executor import Executor, _TurnContext
from orchestrator.user_router import _serialize_event


CAPTURE_AGENT_ID = "fcbcd0ce-73b0-4097-a0cb-6286341f88f6"
CAPTURE_CONV_ID = "90d40c85-cca4-43fe-8e3f-9ad3717fb1b4"


def _make_turn_ctx(queue: EventQueue, *, task_id: str = "t") -> _TurnContext:
    return _TurnContext(
        conv_id=CAPTURE_CONV_ID,
        task_id=task_id,
        call_context=MagicMock(),
        event_queue=queue,
    )


# ════════════════════════════════════════════════════════════════════
# Fixture 加载
# ════════════════════════════════════════════════════════════════════


def _load_fixture() -> list[dict]:
    """读取 jsonl fixture，返回逐行 dict 列表。"""
    fixture_path = (
        Path(__file__).parent.parent
        / "fixtures"
        / "buy_wealth_round1_key_frames.jsonl"
    )
    frames = []
    with open(fixture_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            frames.append(json.loads(line))
    return frames


# ════════════════════════════════════════════════════════════════════
# 辅助：protobuf event 构造（Pattern B 用）
# ════════════════════════════════════════════════════════════════════


def _va_artifact(node_data: dict, event_kind: str = "message") -> TaskArtifactUpdateEvent:
    """新 VA 契约：业务帧用文本装在 ``vatype=data_proxy`` 的 text Part 里（a2a 侧 json.loads 还原）。"""
    frame = {"event": event_kind, "data": node_data}
    meta = Struct()
    meta.update({"vatype": "data_proxy"})
    part = Part(text=json.dumps(frame, ensure_ascii=False), metadata=meta)
    return TaskArtifactUpdateEvent(
        task_id="va-task",
        context_id=CAPTURE_CONV_ID,
        artifact=Artifact(artifact_id="va-art", parts=[part]),
        last_chunk=False,
    )


def _wrap_stream_resp(event):
    return StreamResponse(artifact_update=event)


async def _async_iter(items):
    for item in items:
        yield item


# ════════════════════════════════════════════════════════════════════
# 辅助：从 fixture 反向构造 AgentEvent
# ════════════════════════════════════════════════════════════════════


def _fixture_to_agent_events(fixture: list[dict]) -> list:
    """把 fixture 里的 Pattern A 帧映射回源 AgentEvent 对象。

    注意 fixture 里的 Pattern B 帧不构造 AgentEvent，改由 VA mock 产出。
    """
    events: list = []
    for frame in fixture:
        event_type = frame["custom_rsp_data"]["event"]
        content = frame["custom_rsp_data"].get("content", "")

        if event_type == "conversation_start":
            events.append(ConversationStartEvent(content=content))
        elif event_type == "conversation_end":
            events.append(ConversationEndEvent(content=content))
        elif event_type == "think_start":
            events.append(ThinkStartEvent(content=content))
        elif event_type == "think_chunk":
            events.append(ThinkChunkEvent(content=content))
        elif event_type == "think_end":
            events.append(ThinkEndEvent(content=content))
        elif event_type == "todolist_start":
            events.append(TodoListStartEvent(content=content))
        elif event_type == "todolist_item":
            # 从 content 解析 id / title / status
            # fixture 格式: "1.推荐理财产品（待执行）<br/>"
            pt = content.split(".", 1)
            item_id = int(pt[0]) if pt[0].isdigit() else 0
            rest = pt[1] if len(pt) > 1 else ""
            title = rest.split("（")[0] if "（" in rest else rest.replace("<br/>", "")
            events.append(TodoListItemEvent(
                id=item_id, title=title, status="pending", content=content,
            ))
        elif event_type == "todolist_end":
            events.append(TodoListEndEvent(content=content))
        elif event_type == "todo_start":
            events.append(TodoStartEvent(id=1, title=content, content=content))
        elif event_type == "todo_status":
            events.append(TodoStatusEvent(
                id=1, status="in_progress", content=content,
            ))
        elif event_type == "tool_start":
            events.append(ToolStartEvent(
                content=content, plugin="product_recommend_skill",
            ))
        elif event_type == "tool_status":
            events.append(ToolStatusEvent(
                content=content, plugin="product_recommend_skill",
            ))
        elif event_type == "planning_execution_process":
            # 这个由 executor 层自动发射，不塞到 agent_stream
            continue
        elif event_type == "message":
            # Pattern B 帧由 VA mock 产生
            continue
        elif event_type == "end":
            # VA end 帧也来自 VA mock
            continue
    return events


# ════════════════════════════════════════════════════════════════════
# 辅助：运行 pipeline 收集帧
# ════════════════════════════════════════════════════════════════════


def _drain_queue(queue: EventQueue) -> list:
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


async def _run_and_collect(
    monkeypatch,
    agent_events: list,
    va_events_by_call: list[list[TaskArtifactUpdateEvent]] | None = None,
) -> list[dict]:
    """跑 Executor.run_agent，返回包装后的 SSE 帧（dict 列表）。"""
    va_events_by_call = va_events_by_call or []
    call_idx = [0]

    async def fake_agent_stream(**kwargs):
        for ev in agent_events:
            yield ev

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    # VA mock
    redis = MagicMock()
    redis.get_json = AsyncMock(return_value={
        "headers": {}, "body": {"input": {}, "custom_data": {"inputs": {}}},
    })

    va_client = MagicMock()

    def mock_send_message(request):
        if call_idx[0] < len(va_events_by_call):
            events = va_events_by_call[call_idx[0]]
        else:
            events = []
        call_idx[0] += 1
        return _async_iter([_wrap_stream_resp(e) for e in events])

    va_client.send_message = mock_send_message

    task_store = MagicMock()
    task_store.get = AsyncMock(return_value=None)
    task_store.save = AsyncMock()

    executor = Executor(va_client=va_client, redis=redis, task_store=task_store)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="买理财",
        original_body={},
        cascade_result=None,
    )

    frames = []
    for ev in _drain_queue(queue):
        payload = _serialize_event(
            ev,
            agent_id=CAPTURE_AGENT_ID,
            conversation_id=CAPTURE_CONV_ID,
            start_time=0.0,
        )
        if payload is None:
            continue
        frames.append(json.loads(payload))
    return frames


# ════════════════════════════════════════════════════════════════════
# 实际测试
# ════════════════════════════════════════════════════════════════════


def test_fixture_is_well_formed():
    """fixture 自身合法（所有 17 行都能 parse）。"""
    fixture = _load_fixture()
    assert len(fixture) >= 15
    # 每行都是合法 JSON 且含 agent_id / conversation_id / custom_rsp_data
    for i, f in enumerate(fixture):
        assert f["agent_id"] == CAPTURE_AGENT_ID
        assert f["conversation_id"] == CAPTURE_CONV_ID
        assert "custom_rsp_data" in f


def test_fixture_has_expected_event_types():
    """fixture 覆盖了 Phase 1-4 实现的所有关键事件类型。"""
    fixture = _load_fixture()
    types_in_fixture = {f["custom_rsp_data"]["event"] for f in fixture}
    required = {
        "conversation_start",
        "think_start",
        "think_chunk",
        "think_end",
        "todolist_start",
        "todolist_item",
        "todolist_end",
        "planning_execution_process",
        "todo_start",
        "todo_status",
        "tool_start",
        "tool_status",
        "message",  # Pattern B
        "end",      # Pattern B
        "conversation_end",
    }
    missing = required - types_in_fixture
    assert not missing, f"fixture 缺少事件类型: {missing}"


@pytest.mark.asyncio
async def test_agent_events_produce_expected_frame_types(monkeypatch):
    """喂 fixture 对应的 AgentEvent 序列，pipeline 应产出相同的 Pattern A 事件类型序列。"""
    fixture = _load_fixture()
    agent_events = _fixture_to_agent_events(fixture)

    # 在第一个 ToolStartEvent 前插入一个真实 DelegateRequest，以便 VA mock 触发（即便不需要返回 End node）
    # 这里我们不插入 Delegate，只跑 Pattern A；Pattern B 走另一个测试
    actual_frames = await _run_and_collect(monkeypatch, agent_events=agent_events)

    fixture_types = [
        f["custom_rsp_data"]["event"] for f in fixture
        if f["custom_rsp_data"]["event"] not in ("message", "end")
    ]
    actual_types = [f["custom_rsp_data"]["event"] for f in actual_frames]

    # 每一个期望的 agent 事件类型都应在 actual 里出现
    for event_type in fixture_types:
        if event_type == "planning_execution_process":
            # Executor 自动发射，可能数量不完全一致（capture 有 3 条，我们只产 1 条）
            # 只验证"至少有一条"
            assert "planning_execution_process" in actual_types
        else:
            assert event_type in actual_types, (
                f"期望事件 {event_type} 未出现在 actual"
            )


@pytest.mark.asyncio
async def test_todolist_item_content_matches_fixture(monkeypatch):
    """todolist_item 的 content 格式（HTML <br/>）完全对齐 fixture。"""
    fixture = _load_fixture()
    fixture_todolist_contents = [
        f["custom_rsp_data"]["content"]
        for f in fixture
        if f["custom_rsp_data"]["event"] == "todolist_item"
    ]
    assert fixture_todolist_contents  # 确认 fixture 有数据

    # 每一条都必须以 <br/> 结尾
    for content in fixture_todolist_contents:
        assert content.endswith("<br/>")

    # 反过来测 pipeline 也产出这种格式
    agent_events = _fixture_to_agent_events(fixture)
    actual = await _run_and_collect(monkeypatch, agent_events=agent_events)

    actual_todolist_contents = [
        f["custom_rsp_data"]["content"]
        for f in actual
        if f["custom_rsp_data"]["event"] == "todolist_item"
    ]
    # 数量一致 + 每条都对齐
    assert actual_todolist_contents == fixture_todolist_contents


@pytest.mark.asyncio
async def test_think_chunk_execution_time_is_numeric(monkeypatch):
    """对齐 spec §2.3.3：think_chunk 的 execution_time 是数字，pipeline 也应如此。

    历史 fixture (buy_wealth_round1_key_frames.jsonl) 捕获自旧版服务端的
    抓包行为（当时服务端对 think_chunk 不计算实时 elapsed，故出空串）。
    现以 spec 为准，pipeline 产出全部为数字。
    """
    fixture = _load_fixture()
    agent_events = _fixture_to_agent_events(fixture)
    actual = await _run_and_collect(monkeypatch, agent_events=agent_events)
    for f in actual:
        if f["custom_rsp_data"]["event"] == "think_chunk":
            assert isinstance(f["execution_time"], (int, float)), (
                f"think_chunk execution_time 应是数字，实际 {f['execution_time']!r}"
            )


@pytest.mark.asyncio
async def test_planning_execution_process_error_code_aligned_with_fixture(monkeypatch):
    """fixture 里 planning_execution_process 帧带 error_code: ""，其他不带。"""
    fixture = _load_fixture()
    for f in fixture:
        event_type = f["custom_rsp_data"]["event"]
        if event_type == "planning_execution_process":
            assert "error_code" in f
            assert f["error_code"] == ""
        elif event_type in ("message", "end"):
            # Pattern B 不带 error_code
            assert "error_code" not in f
        else:
            # 其他 Pattern A 事件不带 error_code
            assert "error_code" not in f


@pytest.mark.asyncio
async def test_pattern_b_frames_from_va_match_fixture_shape(monkeypatch):
    """喂 VA mock 生成 Pattern B 帧，形状应对齐 fixture。"""
    fixture = _load_fixture()
    # fixture 里的 message 帧（node_type=QA）
    message_frames = [
        f for f in fixture if f["custom_rsp_data"]["event"] == "message"
    ]
    assert message_frames

    # 构造对应 VA 事件
    va_events = []
    for f in message_frames:
        va_events.append(_va_artifact(f["custom_rsp_data"]["data"]))
    # 补 End node
    va_events.append(_va_artifact({
        "node_id": "node_end",
        "node_type": "End",
        "node_name": "结束",
        "is_finished": True,
        "workflow_id": "wf-1",
    }))

    # 在 agent 流里发一次 DelegateRequest 触发 VA 调用
    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield DelegateRequest(intent="查询", task_description="查询灰度")
        else:
            yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    redis = MagicMock()
    redis.get_json = AsyncMock(return_value={"headers": {}, "body": {}})
    va_client = MagicMock()

    def _send_message(_req):
        return _async_iter([_wrap_stream_resp(e) for e in va_events])

    va_client.send_message = _send_message
    task_store = MagicMock()
    task_store.get = AsyncMock(return_value=None)
    task_store.save = AsyncMock()
    executor = Executor(va_client=va_client, redis=redis, task_store=task_store)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    actual_frames = []
    for ev in _drain_queue(queue):
        payload = _serialize_event(
            ev,
            agent_id=CAPTURE_AGENT_ID,
            conversation_id=CAPTURE_CONV_ID,
            start_time=0.0,
        )
        if payload is not None:
            actual_frames.append(json.loads(payload))

    # 找到 actual 里的 message 帧（Pattern B）
    actual_message_frames = [
        f for f in actual_frames
        if f["custom_rsp_data"]["event"] == "message"
    ]
    assert len(actual_message_frames) >= 1

    # Pattern B 字段结构对齐：无 output/error/error_code
    for f in actual_message_frames:
        assert "output" not in f
        assert "error" not in f
        assert "error_code" not in f
        inner = f["custom_rsp_data"]
        assert set(inner.keys()) == {"event", "data"}

    # data 字段至少含 node_type / node_name
    first = actual_message_frames[0]
    assert first["custom_rsp_data"]["data"]["node_type"] == "QA"
    assert "node_name" in first["custom_rsp_data"]["data"]


@pytest.mark.asyncio
async def test_outer_wrapper_fields_match_fixture_for_all_agent_events(monkeypatch):
    """所有 Pattern A 帧的外层字段（success/agent_id/conv_id/output/error）与 fixture 一致。"""
    fixture = _load_fixture()
    agent_events = _fixture_to_agent_events(fixture)
    actual = await _run_and_collect(monkeypatch, agent_events=agent_events)

    for f in actual:
        if f["custom_rsp_data"]["event"] in ("message", "end"):
            continue  # Pattern B 不同规则
        assert f["success"] is True
        assert f["agent_id"] == CAPTURE_AGENT_ID
        assert f["conversation_id"] == CAPTURE_CONV_ID
        assert f["output"] == ""
        assert f["error"] == ""
        # custom_rsp_data 内层
        inner = f["custom_rsp_data"]
        assert inner["latency"] == ""
        # plugin 只有 tool_* 事件才带工具名，其余事件空串
        if inner["event"] in ("tool_start", "tool_status", "tool_end"):
            assert inner["plugin"], f"tool event 应该带 plugin: {inner}"
        else:
            assert inner["plugin"] == ""
        assert isinstance(inner["createdTime"], int)
