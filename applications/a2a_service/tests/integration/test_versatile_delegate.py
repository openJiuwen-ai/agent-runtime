"""
Task B: DelegateRequest → VA → cascade 完整路径集成测试。

覆盖 Pattern B 的核心主路径：
  1. agent 发出 DelegateRequest → Executor 调 VA
  2. VA 返回含 End node 的帧流 → cascade 续轮
  3. step_counter 跨 cascade 递增
  4. va_workflow_result_node 命中的节点被过滤（不推给前端），其 text 成为 cascade_result
  5. 其他节点正常走到 event_queue
  6. VA 未完成（无 End node）→ 挂起为 INPUT_REQUIRED（未在此文件测，见 _task_store_save 行为）
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
    DelegateRequest,
    ThinkStartEvent,
    ToolStartEvent,
    ToolEndEvent,
    ToolStatusEvent,
)
from config import get_settings
from orchestrator.executor import Executor


CONV_ID = "conv-delegate-1"
TASK_ID = "task-delegate-1"
DEFAULT_FILTERED_NODE = "GXZQAResponseNode"


def _enable_filtered_node(monkeypatch, node_name: str = DEFAULT_FILTERED_NODE) -> None:
    """Set va_workflow_result_node on the cached Settings for this test only."""
    monkeypatch.setattr(get_settings(), "va_workflow_result_node", node_name)


# ════════════════════════════════════════════════════════════════════
# 辅助：构造 VA 流 mock
# ════════════════════════════════════════════════════════════════════


def _data_part(data: dict) -> Part:
    struct = Struct()
    struct.update(data)
    value = Value()
    value.struct_value.CopyFrom(struct)
    part = Part()
    part.data.CopyFrom(value)
    return part


def _va_artifact_event(node_data: dict) -> TaskArtifactUpdateEvent:
    """构造 VA 返回的 TaskArtifactUpdateEvent（解包后的 workflow message 帧）。

    data part 形状：``{"event": "message", "data": <node_data>}``
    """
    wrapped = {"event": "message", "data": node_data}
    return TaskArtifactUpdateEvent(
        task_id="va-task-1",
        context_id=CONV_ID,
        artifact=Artifact(artifact_id=f"va-art-{id(node_data)}", parts=[_data_part(wrapped)]),
        last_chunk=False,
    )


def _wrap_as_stream_resp(event: TaskArtifactUpdateEvent) -> SimpleNamespace:
    """模拟 VA client 返回的 oneof stream_resp 对象。"""
    return SimpleNamespace(
        WhichOneof=lambda field: "artifact_update" if field == "payload" else None,
        artifact_update=event,
    )


async def _async_iter(items):
    for item in items:
        yield item


# ════════════════════════════════════════════════════════════════════
# 辅助：构造 Executor
# ════════════════════════════════════════════════════════════════════


def _make_executor_with_va_stream(va_events: list[TaskArtifactUpdateEvent]) -> Executor:
    """返回一个 Executor，其 _va_client.send_message 会 yield 指定的 VA 事件。"""
    # redis mock：返回模拟的 cached body
    redis = MagicMock()
    redis.get_json = AsyncMock(return_value={
        "headers": {},
        "body": {
            "input": {"query": "原始用户输入"},
            "custom_data": {"inputs": {"query": "原始用户输入", "intent": ""}},
            "stream": True,
        },
    })

    # va_client mock：send_message 返回 async iterator of stream_resp
    va_client = MagicMock()
    def mock_send_message(request):
        stream_resps = [_wrap_as_stream_resp(e) for e in va_events]
        return _async_iter(stream_resps)
    va_client.send_message = mock_send_message

    # task_store mock
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


# ════════════════════════════════════════════════════════════════════
# 测试 1：VA 返回含 End node → cascade 触发
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delegate_with_end_node_triggers_cascade(monkeypatch):
    """VA 响应含 End node → Executor 应调 agent_stream 第二次（cascade）。"""
    va_events = [
        # 节点数据帧：INSTRUCTIONKEY=GET_GRAY_INFO
        _va_artifact_event({
            "text": '{"SPTRANSRETCODE":"00009","INSTRUCTIONKEY":"GET_GRAY_INFO"}',
            "index": "0",
            "node_id": "node_gray",
            "node_type": "QA",
            "node_name": "问答_获取灰度策略",
            "workflow_id": "wf-1",
        }),
        # va_workflow_result_node 帧（默认 GXZQAResponseNode）→ 被过滤
        _va_artifact_event({
            "text": "QA结果文本",
            "node_id": "node_qa_result",
            "node_type": "QA",
            "node_name": "GXZQAResponseNode",
            "workflow_id": "wf-1",
        }),
        # End node 帧
        _va_artifact_event({
            "text": "",
            "node_id": "node_end",
            "node_type": "End",
            "node_name": "结束",
            "is_finished": True,
            "workflow_id": "wf-1",
        }),
    ]
    executor = _make_executor_with_va_stream(va_events)

    # 记录 agent_stream 调用次数
    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # 第一次：agent 产生 DelegateRequest
            yield ConversationStartEvent()
            yield ThinkStartEvent()
            yield DelegateRequest(
                intent="查询灰度策略",
                task_description="查询课题版灰度策略",
            )
        else:
            # 第二次（cascade）：直接结束
            yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID,
        task_id=TASK_ID,
        call_context=MagicMock(),
        query="测试",
        original_body={},
        event_queue=queue,
        cascade_result=None,
    )

    # agent_stream 被调了两次：首轮 + cascade 续轮
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_delegate_emits_pattern_b_frames_excluding_filtered(monkeypatch):
    """VA 返回的节点帧都 enqueue 到 event_queue，但 va_workflow_result_node 被过滤。"""
    _enable_filtered_node(monkeypatch)
    va_events = [
        _va_artifact_event({
            "node_id": "n1",
            "node_type": "QA",
            "node_name": "问答_获取灰度策略",  # 非过滤节点
            "text": "data1",
            "workflow_id": "wf-1",
        }),
        _va_artifact_event({
            "node_id": "n2",
            "node_type": "QA",
            "node_name": "GXZQAResponseNode",  # 默认被过滤
            "text": "suppressed",
            "workflow_id": "wf-1",
        }),
        _va_artifact_event({
            "node_id": "node_end",
            "node_type": "End",
            "node_name": "结束",
            "is_finished": True,
            "workflow_id": "wf-1",
        }),
    ]
    executor = _make_executor_with_va_stream(va_events)

    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield DelegateRequest(
                intent="查询", task_description="查询灰度",
            )
        else:
            yield ConversationEndEvent()  # cascade 轮直接结束，防死循环

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id=TASK_ID, call_context=MagicMock(),
        query="x", original_body={}, event_queue=queue, cascade_result=None,
    )

    enqueued = _drain_queue(queue)

    # 抽出 VA artifact 节点的 node_name（帧已解包为 {event, data: {node...}}）
    va_node_names = []
    for ev in enqueued:
        if isinstance(ev, TaskArtifactUpdateEvent):
            for part in ev.artifact.parts:
                if part.WhichOneof("content") == "data":
                    frame = MessageToDict(part.data)
                    node = frame.get("data") if isinstance(frame, dict) else None
                    if isinstance(node, dict) and node.get("node_type") in ("QA", "End"):
                        va_node_names.append(node.get("node_name"))
                        break
    # GXZQAResponseNode 被过滤，不在列表里
    assert "GXZQAResponseNode" not in va_node_names
    # 其他节点应该都在
    assert "问答_获取灰度策略" in va_node_names
    assert "结束" in va_node_names  # End node 本身不被过滤


@pytest.mark.asyncio
async def test_delegate_passes_qa_result_as_cascade_input(monkeypatch):
    """被过滤的 GXZQAResponseNode 的 text 应作为 cascade_result 传给第二轮 agent_stream。"""
    _enable_filtered_node(monkeypatch)
    va_events = [
        _va_artifact_event({
            "node_id": "n1",
            "node_type": "QA",
            "node_name": "GXZQAResponseNode",  # 过滤节点，text 用于 cascade
            "text": "QA节点的最终文本结果",
            "workflow_id": "wf-1",
        }),
        _va_artifact_event({
            "node_id": "node_end",
            "node_type": "End",
            "node_name": "结束",
            "is_finished": True,
            "workflow_id": "wf-1",
        }),
    ]
    executor = _make_executor_with_va_stream(va_events)

    received_cascade_result = [None]

    async def fake_agent_stream(**kwargs):
        received_cascade_result[0] = kwargs.get("cascade_result")
        if received_cascade_result[0] is None:
            # 首轮：产生 Delegate
            yield DelegateRequest(intent="查", task_description="查")
        else:
            # Cascade 轮：结束
            yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id=TASK_ID, call_context=MagicMock(),
        query="x", original_body={}, event_queue=queue, cascade_result=None,
    )

    # cascade 轮被调时，cascade_result = {"workflow_result": "QA节点的最终文本结果"}
    assert received_cascade_result[0] is not None
    assert received_cascade_result[0] == {"workflow_result": "QA节点的最终文本结果"}


# ════════════════════════════════════════════════════════════════════
# 测试 2：step_counter 跨 cascade 递增（Phase 3 合约）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step_counter_continues_across_cascade(monkeypatch):
    """首轮的 tool + delegate 计 2 步，cascade 轮的 tool 计第 3 步。"""
    _enable_filtered_node(monkeypatch)
    va_events = [
        _va_artifact_event({
            "node_id": "n1", "node_type": "QA",
            "node_name": "GXZQAResponseNode",
            "text": "cascade-data",
            "workflow_id": "wf-1",
        }),
        _va_artifact_event({
            "node_id": "node_end", "node_type": "End", "node_name": "结束",
            "is_finished": True, "workflow_id": "wf-1",
        }),
    ]
    executor = _make_executor_with_va_stream(va_events)

    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield ToolStartEvent(content="第一步工具", plugin="tool_a")
            yield ToolStatusEvent(content="第一步工具", plugin="tool_a")
            yield ToolEndEvent(content="完成 tool_a", plugin="tool_a")
            yield DelegateRequest(intent="查", task_description="查灰度")
        else:
            yield ToolStartEvent(content="第三步工具", plugin="tool_c")
            yield ToolStatusEvent(content="第三步工具", plugin="tool_c")
            yield ToolEndEvent(content="完成 tool_c", plugin="tool_c")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id=TASK_ID, call_context=MagicMock(),
        query="x", original_body={}, event_queue=queue, cascade_result=None,
    )

    # 收集所有 planning_execution_process 的 content
    planning_contents = []
    for ev in _drain_queue(queue):
        if isinstance(ev, TaskArtifactUpdateEvent):
            for part in ev.artifact.parts:
                if part.WhichOneof("content") == "data":
                    data = MessageToDict(part.data)
                    if data.get("type") == "planning_execution_process":
                        planning_contents.append(data.get("content", ""))

    # 应该有 3 条：ToolStart(tool_a), Delegate(versatile_proxy), ToolStart(tool_c)
    assert len(planning_contents) == 3
    assert "步骤1" in planning_contents[0]
    assert "(tool=tool_a)" in planning_contents[0]
    assert "步骤2" in planning_contents[1]
    assert "(tool=adapter:versatile_proxy)" in planning_contents[1]
    assert "步骤3" in planning_contents[2]
    assert "(tool=tool_c)" in planning_contents[2]


# ════════════════════════════════════════════════════════════════════
# 测试 3：VA 未完成（无 End node）→ 不 cascade
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delegate_without_end_node_does_not_cascade(monkeypatch):
    """VA 响应无 End node → agent_stream 只被调一次（不 cascade）。"""
    va_events = [
        _va_artifact_event({
            "node_id": "n1", "node_type": "QA",
            "node_name": "某中间节点",
            "text": "incomplete",
            "workflow_id": "wf-1",
        }),
        # 无 End node
    ]
    executor = _make_executor_with_va_stream(va_events)
    # 让 task_store.get 返回 None（避免 metadata 更新路径失败）
    executor._task_store.get = AsyncMock(return_value=None)

    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        yield DelegateRequest(intent="查", task_description="查")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor._run_agent(
        conv_id=CONV_ID, task_id=TASK_ID, call_context=MagicMock(),
        query="x", original_body={}, event_queue=queue, cascade_result=None,
    )

    # 只调一次（没有 cascade）
    assert call_count[0] == 1
