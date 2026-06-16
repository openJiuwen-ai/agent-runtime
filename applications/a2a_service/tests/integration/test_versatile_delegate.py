# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Task B: DelegateRequest → VA → cascade 完整路径集成测试。

覆盖 Pattern B 的核心主路径：
  1. agent 发出 DelegateRequest → Executor 调 VA
  2. VA 通过 TaskStatusUpdateEvent(COMPLETED) 标识工作流结束 → cascade 续轮
  3. step_counter 跨 cascade 递增
  4. va_workflow_result_node 命中的节点其 text 走 COMPLETED.message 的 vatype=workflow_result Part 作为 cascade_result
  5. 其他节点正常通过 TaskArtifactUpdateEvent + text Part(vatype=data_proxy) 走到 event_queue
  6. VA 未完成（无 COMPLETED 事件）→ 不 cascade（用 INPUT_REQUIRED 或自然结束）
  7. VA 上游报错 → 通过 TaskStatusUpdateEvent(FAILED) + vatype=upstream_error Part 携带错误详情

协议契约：
  - 数据帧：TaskArtifactUpdateEvent，artifact.parts = [Part(text=json_str,
      metadata={vatype:"data_proxy"})]
  - 结束事件：TaskStatusUpdateEvent(state=COMPLETED, message=Message(parts=[
      Part(text=qa_text, metadata={vatype:"workflow_result"})]))
  - 失败事件：TaskStatusUpdateEvent(state=FAILED, message=Message(parts=[
      Part(text=err_json, metadata={vatype:"upstream_error"})]))
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    ROLE_AGENT,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_WORKING,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
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
from orchestrator.executor import Executor, _TurnContext


CONV_ID = "conv-delegate-1"
TASK_ID = "task-delegate-1"
DEFAULT_FILTERED_NODE = "GXZQAResponseNode"


def _make_turn_ctx(queue: EventQueue) -> _TurnContext:
    """Build a default ``_TurnContext`` used by tests in this module."""
    return _TurnContext(
        conv_id=CONV_ID,
        task_id=TASK_ID,
        call_context=MagicMock(),
        event_queue=queue,
    )


def _enable_filtered_node(monkeypatch, node_name: str = DEFAULT_FILTERED_NODE) -> None:
    """Set va_workflow_result_node on the cached Settings for this test only."""
    monkeypatch.setattr(get_settings(), "va_workflow_result_node", node_name)


# ════════════════════════════════════════════════════════════════════
# 辅助：构造 VA 流 mock（新协议：text Part + vatype + Status 事件）
# ════════════════════════════════════════════════════════════════════


def _text_part(text: str, vatype: str | None = None) -> Part:
    """构造 text Part，可选 metadata.vatype。"""
    metadata = None
    if vatype:
        metadata = Struct()
        metadata.update({"vatype": vatype})
    part = Part()
    part.text = text
    if metadata is not None:
        part.metadata.CopyFrom(metadata)
    return part


def _va_data_proxy_event(node_data: dict) -> TaskArtifactUpdateEvent:
    """构造 VA 透传的数据帧：text Part(vatype=data_proxy)，text 是 JSON 字符串。

    对应 VA 侧 _make_text_part(event.data_proxy.raw_data, "data_proxy") 的产物。
    node_data 形如 ``{"event": "message", "data": {node_type, node_name, text, ...}}``
    或扁平 ``{node_type, node_name, text, ...}`` —— 这里默认采用前者，与一级控制器实际帧一致。
    """
    wrapped = {"event": "message", "data": node_data}
    payload = json.dumps(wrapped, ensure_ascii=False)
    return TaskArtifactUpdateEvent(
        task_id="va-task-1",
        context_id=CONV_ID,
        artifact=Artifact(
            artifact_id=f"va-art-{id(node_data)}",
            parts=[_text_part(payload, vatype="data_proxy")],
        ),
        last_chunk=False,
    )


def _va_completed_event(workflow_result: str | None = None) -> TaskStatusUpdateEvent:
    """构造 VA 工作流完成事件：TaskStatusUpdateEvent(COMPLETED)。

    若 workflow_result 非空，附带 message 含 text Part(vatype=workflow_result)。
    对应 VA 侧 updater.complete(message) 的产物。
    """
    message = None
    if workflow_result:
        message = Message(
            role=ROLE_AGENT,
            message_id="va-msg-completed",
            task_id="va-task-1",
            context_id=CONV_ID,
            parts=[_text_part(workflow_result, vatype="workflow_result")],
        )
    status = TaskStatus(state=TASK_STATE_COMPLETED)
    if message is not None:
        status.message.CopyFrom(message)
    return TaskStatusUpdateEvent(
        task_id="va-task-1",
        context_id=CONV_ID,
        status=status,
    )


def _va_failed_event(error_payload: dict | None = None) -> TaskStatusUpdateEvent:
    """构造 VA 工作流失败事件：TaskStatusUpdateEvent(FAILED)。

    若 error_payload 非空，附带 message 含 text Part(vatype=upstream_error)，
    text 是错误 JSON 字符串。对应 VA 侧 updater.failed(message) 的产物。
    """
    message = None
    if error_payload is not None:
        err_json = json.dumps(error_payload, ensure_ascii=False)
        message = Message(
            role=ROLE_AGENT,
            message_id="va-msg-failed",
            task_id="va-task-1",
            context_id=CONV_ID,
            parts=[_text_part(err_json, vatype="upstream_error")],
        )
    status = TaskStatus(state=TASK_STATE_FAILED)
    if message is not None:
        status.message.CopyFrom(message)
    return TaskStatusUpdateEvent(
        task_id="va-task-1",
        context_id=CONV_ID,
        status=status,
    )


def _wrap_as_stream_resp(event) -> SimpleNamespace:
    """模拟 VA client 返回的 oneof stream_resp 对象。

    根据 event 类型设置正确的 oneof 字段，与 protobuf oneof 访问保持一致。
    """
    is_status = isinstance(event, TaskStatusUpdateEvent)
    field_name = "status_update" if is_status else "artifact_update"
    return SimpleNamespace(
        WhichOneof=lambda f, _field=field_name: _field if f == "payload" else None,
        HasField=lambda f, _field=field_name: f == _field,
        artifact_update=None if is_status else event,
        status_update=event if is_status else None,
    )


async def _async_iter(items):
    for item in items:
        yield item


# ════════════════════════════════════════════════════════════════════
# 辅助：构造 Executor
# ════════════════════════════════════════════════════════════════════


def _make_executor_with_va_stream(va_events: list) -> Executor:
    """返回一个 Executor，其 _va_client.send_message 会 yield 指定的 VA 事件。

    va_events 可以混合 TaskArtifactUpdateEvent + TaskStatusUpdateEvent，
    按列表顺序依次 yield 给消费方。
    """
    redis = MagicMock()
    redis.get_json = AsyncMock(return_value={
        "headers": {},
        "body": {
            "input": {"query": "原始用户输入"},
            "custom_data": {"inputs": {"query": "原始用户输入", "intent": ""}},
            "stream": True,
        },
    })

    va_client = MagicMock()

    def mock_send_message(request):
        stream_resps = [_wrap_as_stream_resp(e) for e in va_events]
        return _async_iter(stream_resps)

    va_client.send_message = mock_send_message

    task_store = MagicMock()
    task_store.get = AsyncMock(return_value=None)
    task_store.save = AsyncMock()

    return Executor(va_client=va_client, redis=redis, task_store=task_store)


def _is_event_with_state(event, state) -> bool:
    """判断事件是否是 ``TaskStatusUpdateEvent`` 且处于指定状态。"""
    return (
        isinstance(event, TaskStatusUpdateEvent)
        and event.status is not None
        and event.status.state == state
    )


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


# ════════════════════════════════════════════════════════════════════
# 测试 1：VA 返回 COMPLETED → cascade 触发
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delegate_with_completed_event_triggers_cascade(monkeypatch):
    """VA 流以 TaskStatusUpdateEvent(COMPLETED) 结束 → Executor 应调 agent_stream 第二次（cascade）。"""
    va_events = [
        _va_data_proxy_event({
            "text": '{"SPTRANSRETCODE":"00009","INSTRUCTIONKEY":"GET_GRAY_INFO"}',
            "index": "0",
            "node_id": "node_gray",
            "node_type": "QA",
            "node_name": "问答_获取灰度策略",
            "workflow_id": "wf-1",
        }),
        # End 节点数据帧（仅作为前端可见的"流到达 End"信号，由 VA 透传）
        _va_data_proxy_event({
            "text": "",
            "node_id": "node_end",
            "node_type": "End",
            "node_name": "结束",
            "is_finished": True,
            "workflow_id": "wf-1",
        }),
        # 真正驱动 has_end_node = True 的是 COMPLETED 状态事件
        _va_completed_event(workflow_result=None),
    ]
    executor = _make_executor_with_va_stream(va_events)

    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield ConversationStartEvent()
            yield ThinkStartEvent()
            yield DelegateRequest(
                intent="查询灰度策略",
                task_description="查询课题版灰度策略",
            )
        else:
            yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="测试",
        original_body={},
        cascade_result=None,
    )

    # agent_stream 被调了两次：首轮 + cascade 续轮
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_delegate_forwards_data_proxy_frames(monkeypatch):
    """VA 返回的 data_proxy text Part 帧都 enqueue 到 event_queue。"""
    _enable_filtered_node(monkeypatch)
    va_events = [
        _va_data_proxy_event({
            "node_id": "n1",
            "node_type": "QA",
            "node_name": "问答_获取灰度策略",
            "text": "data1",
            "workflow_id": "wf-1",
        }),
        _va_data_proxy_event({
            "node_id": "node_end",
            "node_type": "End",
            "node_name": "结束",
            "is_finished": True,
            "workflow_id": "wf-1",
        }),
        _va_completed_event(workflow_result=None),
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
            yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    enqueued = _drain_queue(queue)

    # _forward_artifact 会把 text Part(vatype=data_proxy) 的 JSON 解析后转为 data Part 推前端
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
    # 全部 data_proxy 帧都被转发
    assert "问答_获取灰度策略" in va_node_names
    assert "结束" in va_node_names


@pytest.mark.asyncio
async def test_delegate_passes_workflow_result_as_cascade_input(monkeypatch):
    """COMPLETED.message 的 vatype=workflow_result Part 应作为 cascade_result 传给第二轮 agent_stream。"""
    _enable_filtered_node(monkeypatch)
    qa_text = "QA节点的最终文本结果"
    va_events = [
        _va_data_proxy_event({
            "node_id": "n1",
            "node_type": "QA",
            "node_name": "问答_中间节点",
            "text": "intermediate",
            "workflow_id": "wf-1",
        }),
        # workflow_result 通过 COMPLETED 事件的 message 携带
        _va_completed_event(workflow_result=qa_text),
    ]
    executor = _make_executor_with_va_stream(va_events)

    received_cascade_result = [None]

    async def fake_agent_stream(**kwargs):
        received_cascade_result[0] = kwargs.get("cascade_result")
        if received_cascade_result[0] is None:
            yield DelegateRequest(intent="查", task_description="查")
        else:
            yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    assert received_cascade_result[0] is not None
    assert received_cascade_result[0] == {"workflow_result": qa_text}


# ════════════════════════════════════════════════════════════════════
# 测试 2：step_counter 跨 cascade 递增（Phase 3 合约）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_step_counter_continues_across_cascade(monkeypatch):
    """首轮的 tool + delegate 计 2 步，cascade 轮的 tool 计第 3 步。"""
    _enable_filtered_node(monkeypatch)
    va_events = [
        _va_data_proxy_event({
            "node_id": "n1", "node_type": "QA",
            "node_name": "问答_中间节点",
            "text": "intermediate",
            "workflow_id": "wf-1",
        }),
        _va_completed_event(workflow_result="cascade-data"),
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
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

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
# 测试 3：VA 未完成（无 COMPLETED 事件）→ 不 cascade
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delegate_without_completed_event_does_not_cascade(monkeypatch):
    """VA 流自然结束但未发 COMPLETED → agent_stream 只被调一次（不 cascade）。"""
    va_events = [
        _va_data_proxy_event({
            "node_id": "n1", "node_type": "QA",
            "node_name": "某中间节点",
            "text": "incomplete",
            "workflow_id": "wf-1",
        }),
        # 无 TaskStatusUpdateEvent(COMPLETED)
    ]
    executor = _make_executor_with_va_stream(va_events)

    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        yield DelegateRequest(intent="查", task_description="查")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    # 只调一次（没有 cascade）
    assert call_count[0] == 1


# ════════════════════════════════════════════════════════════════════
# 测试 4：VA 上游报错（FAILED + upstream_error）→ 终态 FAILED + 错误透传
# ════════════════════════════════════════════════════════════════════


def _make_executor_with_real_task(
    va_events: list,
    *,
    initial_va_task_id: str = "stale-va-task-id",
) -> tuple[Executor, Task, MagicMock]:
    """变体：task_store.get 返回真实 Task，验证 save 时落了哪些字段。"""
    fake_task = Task(
        id=TASK_ID,
        context_id=CONV_ID,
        status=TaskStatus(state=TASK_STATE_WORKING),
    )
    fake_task.metadata.update({"va_task_id": initial_va_task_id})

    redis = MagicMock()
    redis.get_json = AsyncMock(return_value={
        "headers": {},
        "body": {
            "input": {"query": "原始用户输入"},
            "custom_data": {"inputs": {"query": "原始用户输入", "intent": ""}},
            "stream": True,
        },
    })

    va_client = MagicMock()

    def mock_send_message(request):
        return _async_iter([_wrap_as_stream_resp(e) for e in va_events])

    va_client.send_message = mock_send_message

    task_store = MagicMock()
    task_store.get = AsyncMock(return_value=fake_task)
    task_store.save = AsyncMock()

    return Executor(va_client=va_client, redis=redis, task_store=task_store), fake_task, task_store


@pytest.mark.asyncio
async def test_va_failed_event_marks_task_failed_and_clears_task_id(monkeypatch):
    """VA 流以 FAILED 事件结束时：task 落 FAILED + va_task_id 清空，破解 conv_id 锁死。"""
    err_msg = "执行报错，错误码：103104，错误信息：'NoneType' object has no attribute 'content'"
    va_events = [_va_failed_event({"code": "103104", "message": err_msg})]
    executor, task, task_store = _make_executor_with_real_task(va_events)

    async def fake_agent_stream(**kwargs):
        yield DelegateRequest(intent="查", task_description="查灰度")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    save_calls = task_store.save.call_args_list
    assert len(save_calls) >= 1
    saved_task = save_calls[-1][0][0]
    assert saved_task.status.state == TASK_STATE_FAILED
    saved_meta = MessageToDict(saved_task.metadata)
    assert saved_meta.get("va_task_id", "") == ""


@pytest.mark.asyncio
async def test_va_failed_event_enqueues_failed_status_with_message(monkeypatch):
    """VA 流以 FAILED 事件结束时：北向 enqueue TaskStatusUpdateEvent(FAILED) 并带错误描述。

    user_router._extract_event_meta 会把 status.message.text 转为 interrupt_start.content。
    """
    err_msg = "执行报错，错误码：103104，错误信息：xxx"
    va_events = [_va_failed_event({"code": "103104", "message": err_msg})]
    executor, _task, _task_store = _make_executor_with_real_task(va_events)

    async def fake_agent_stream(**kwargs):
        yield DelegateRequest(intent="查", task_description="查")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    enqueued = _drain_queue(queue)
    failed_events = [e for e in enqueued if _is_event_with_state(e, TASK_STATE_FAILED)]
    assert len(failed_events) == 1, f"期望 1 个 FAILED 事件，实际 {len(failed_events)} 个"
    fe = failed_events[0]
    assert fe.status.message, "FAILED 事件必须带 status.message 让前端拿到错误描述"
    text_chunks = [
        p.text for p in fe.status.message.parts
        if p.WhichOneof("content") == "text"
    ]
    assert any(err_msg in t for t in text_chunks), (
        f"FAILED 事件 message text 应含错误描述（含错误码 103104 与具体 message），实际为 {text_chunks!r}"
    )


@pytest.mark.asyncio
async def test_va_failed_event_does_not_emit_input_required(monkeypatch):
    """VA FAILED 路径不应再发 INPUT_REQUIRED（避免下次请求走续轮路径锁死 conv_id）。"""
    va_events = [_va_failed_event({"code": "103104", "message": "错误"})]
    executor, _task, _task_store = _make_executor_with_real_task(va_events)

    async def fake_agent_stream(**kwargs):
        yield DelegateRequest(intent="查", task_description="查")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    enqueued = _drain_queue(queue)
    input_required = [e for e in enqueued if _is_event_with_state(e, TASK_STATE_INPUT_REQUIRED)]
    assert len(input_required) == 0, "VA FAILED 路径不应发出 INPUT_REQUIRED 状态事件"


@pytest.mark.asyncio
async def test_va_failed_event_without_payload_falls_back_to_generic_message(monkeypatch):
    """VA FAILED 事件 status.message 为空时，应使用兜底通用错误文案。"""
    va_events = [_va_failed_event(error_payload=None)]
    executor, _task, _task_store = _make_executor_with_real_task(va_events)

    async def fake_agent_stream(**kwargs):
        yield DelegateRequest(intent="查", task_description="查")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    enqueued = _drain_queue(queue)
    failed_events = [e for e in enqueued if _is_event_with_state(e, TASK_STATE_FAILED)]
    assert len(failed_events) == 1
    fe = failed_events[0]
    text_chunks = [
        p.text for p in fe.status.message.parts
        if p.WhichOneof("content") == "text"
    ]
    # 兜底通用文案
    assert any("VA" in t or "异常" in t for t in text_chunks), (
        f"VA 未携带错误详情时应使用兜底文案，实际为 {text_chunks!r}"
    )


@pytest.mark.asyncio
async def test_va_failed_event_does_not_trigger_cascade(monkeypatch):
    """VA FAILED 不应被当成成功完成 → 不该触发 cascade 续轮。"""
    va_events = [_va_failed_event({"code": "103104", "message": "错"})]
    executor, _task, _task_store = _make_executor_with_real_task(va_events)

    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        yield DelegateRequest(intent="查", task_description="查")

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
    )

    assert call_count[0] == 1, "VA FAILED 路径不应触发 cascade（agent_stream 不应被调用第二次）"
