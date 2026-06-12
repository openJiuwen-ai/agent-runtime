# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

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
    StreamResponse,
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


def _text_part(text: str, vatype: str) -> Part:
    """构造带 ``vatype`` metadata 的 text Part（新 VA sidecar 契约）。"""
    meta = Struct()
    meta.update({"vatype": vatype})
    return Part(text=text, metadata=meta)


def _va_artifact_event(node_data: dict) -> TaskArtifactUpdateEvent:
    """新契约：VA 把业务帧用文本装在 ``vatype=data_proxy`` 的 text Part 里下传。

    text 内容为 ``{"event":"message","data":<node_data>}`` 的 JSON（a2a 侧由
    ``_extract_data_proxy_frames`` 还原）。
    """
    frame = {"event": "message", "data": node_data}
    return TaskArtifactUpdateEvent(
        task_id="va-task-1",
        context_id=CONV_ID,
        artifact=Artifact(
            artifact_id=f"va-art-{id(node_data)}",
            parts=[_text_part(json.dumps(frame, ensure_ascii=False), "data_proxy")],
        ),
        last_chunk=False,
    )


def _va_completed(workflow_result: str | None = None) -> TaskStatusUpdateEvent:
    """新契约：完成走 ``TaskStatusUpdateEvent(COMPLETED)``，带可选 workflow_result 文本 Part。"""
    msg = None
    if workflow_result is not None:
        msg = Message(
            role=ROLE_AGENT, message_id="m",
            parts=[_text_part(workflow_result, "workflow_result")],
        )
    return TaskStatusUpdateEvent(
        task_id="va-task-1", context_id=CONV_ID,
        status=TaskStatus(state=TASK_STATE_COMPLETED, message=msg),
    )


def _va_failed() -> TaskStatusUpdateEvent:
    """新契约：失败走 ``TaskStatusUpdateEvent(FAILED)``（runner 异常或上游报错）。"""
    return TaskStatusUpdateEvent(
        task_id="va-task-1", context_id=CONV_ID,
        status=TaskStatus(state=TASK_STATE_FAILED),
    )


def _wrap_as_stream_resp(event) -> StreamResponse:
    """模拟 VA client 返回的 oneof StreamResponse（artifact_update / status_update）。"""
    if isinstance(event, TaskStatusUpdateEvent):
        return StreamResponse(status_update=event)
    return StreamResponse(artifact_update=event)


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


def _is_event_with_state(event, state) -> bool:
    """判断事件是否是 ``TaskStatusUpdateEvent`` 且处于指定状态。

    抽成单独的 helper 是为了把推导式中包含多个 ``and`` 子句的过滤条件
    简化为单子句形式（参考 G.EXP.04：避免推导式带超过两个子句或多行子句）。
    """
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
# 测试 1：VA 返回含 End node → cascade 触发
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delegate_completed_triggers_cascade(monkeypatch):
    """新契约：VA 发 TaskStatusUpdateEvent(COMPLETED) → Executor 调 agent_stream 第二次（cascade）。"""
    va_events = [
        # 业务中间帧（data_proxy，转发前端）
        _va_artifact_event({
            "node_id": "node_gray", "node_type": "QA",
            "node_name": "问答_获取灰度策略", "text": "...",
        }),
        # 完成终态（带 workflow_result）→ 触发 cascade
        _va_completed("最终结果文本"),
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
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="测试",
        original_body={},
        cascade_result=None,
    )

    # agent_stream 被调了两次：首轮 + cascade 续轮
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_delegate_passes_workflow_result_as_cascade_input(monkeypatch):
    """新契约：COMPLETED 上携带的 ``vatype=workflow_result`` 文本应作为 cascade_result 传给第二轮。

    （旧的 a2a 侧节点过滤 / QA 节点 text 抽取已随同事重构迁到 VA controller。）
    """
    va_events = [_va_completed("QA节点的最终文本结果")]
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
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="x",
        original_body={},
        cascade_result=None,
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
    va_events = [_va_completed("cascade-data")]
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
    # ``_make_executor_with_va_stream`` 内部已将 ``task_store.get`` mock 为
    # ``AsyncMock(return_value=None)``，无需在测试中再次访问 Executor 的私有
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
# 测试 4：VA 上游报错（event=error / event=exception）→ 终态 FAILED
# ════════════════════════════════════════════════════════════════════
# 对齐 AgentEngine：上游 event=exception 视为 workflow_complete 终态
# （versatile_proxy.py:336）；agent-runtime 这里把 event in (error, exception)
# 都识别为终态，避免错误后无 End node → INPUT_REQUIRED → 续轮锁死 conv_id。


def _make_executor_with_real_task(
    va_events: list[TaskArtifactUpdateEvent],
    *,
    initial_va_task_id: str = "stale-va-task-id",
) -> tuple[Executor, Task, MagicMock]:
    """变体：task_store.get 返回真实 Task，验证 save 时落了哪些字段。

    Returns: (executor, task, task_store) — 直接拿 task 看最终持久化状态；
    一并返回 task_store 引用，方便用例 assert ``save`` 调用而不必访问
    ``executor`` 的受保护成员（参考 G.CLS.11）。
    """
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
async def test_va_error_event_marks_task_failed_and_clears_task_id(monkeypatch):
    """新契约：VA 发 TaskStatusUpdateEvent(FAILED) 时，task 落 FAILED + va_task_id 清空，破解 conv_id 锁死。"""
    va_events = [_va_failed()]
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

    # 至少一次 save 调用，最终状态是 FAILED，va_task_id 被清空
    save_calls = task_store.save.call_args_list
    assert len(save_calls) >= 1
    saved_task = save_calls[-1][0][0]
    assert saved_task.status.state == TASK_STATE_FAILED
    saved_meta = MessageToDict(saved_task.metadata)
    assert saved_meta.get("va_task_id", "") == ""


@pytest.mark.asyncio
async def test_va_error_event_enqueues_failed_status_with_message(monkeypatch):
    """新契约：VA 发 FAILED 时北向 enqueue TaskStatusUpdateEvent(FAILED) 并带错误描述。

    新契约下详细错误码留在 VA 侧日志，a2a 侧给通用错误描述（``VA 任务异常终止``）。
    user_router._extract_event_meta 会把 status.message.text 转为 interrupt_start.content。
    """
    va_events = [_va_failed()]
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
    assert any(t.strip() for t in text_chunks), (
        f"FAILED 事件 message text 应非空，实际为 {text_chunks!r}"
    )


@pytest.mark.asyncio
async def test_va_error_event_does_not_emit_input_required(monkeypatch):
    """VA 发 FAILED 时：不应再发 INPUT_REQUIRED（避免下次请求走续轮路径锁死 conv_id）。"""
    va_events = [_va_failed()]
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
    assert len(input_required) == 0, "VA 报错路径不应发出 INPUT_REQUIRED 状态事件"


@pytest.mark.asyncio
async def test_va_failed_does_not_trigger_cascade(monkeypatch):
    """VA 发 FAILED → 不该触发 cascade 续轮（has_end_node 仅 COMPLETED 置位）。"""
    va_events = [_va_failed()]
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

    assert call_count[0] == 1, "VA 报错路径不应触发 cascade（agent_stream 不应被调用第二次）"


@pytest.mark.asyncio
async def test_va_forwarded_events_restamped_to_owner_task_id(monkeypatch):
    """转发的 VA artifact 事件须重盖为本 Agent 的 task_id（而非 VA 自己的 va-task-1）。

    回归：子 Agent 跑在 A2A server 的 TaskManager 下，会校验事件 task_id 与本任务一致。
    若直接转发携带 VA task_id 的事件，会触发 InvalidParamsError
    （"Task in event doesn't match"）→ Consumer Failed → 关流、后续 cascade 事件全丢，
    父侧拿不到真实结果。新契约下 _forward_artifact 以 turn_ctx 新建事件已隐含重盖，
    本用例锁住该保证：所有转发帧的 task_id 必须等于本 Agent 的 TASK_ID（≠ VA 的 va-task-1）。
    """
    va_events = [
        _va_artifact_event({  # data_proxy 业务帧（会被转发），其 task_id 为 VA 的 va-task-1
            "node_id": "node_start", "node_type": "Start",
            "node_name": "开始", "text": "",
        }),
        _va_completed("企业基础信息"),  # 完成终态 → 触发 cascade
    ]
    executor = _make_executor_with_va_stream(va_events)

    call_count = [0]

    async def fake_agent_stream(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield DelegateRequest(intent="基本信息抽取", task_description="小米")
        else:
            yield ConversationEndEvent()

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)

    queue = EventQueue()
    await executor.run_agent(
        _make_turn_ctx(queue),
        query="对小米进行信贷分析",
        original_body={},
        cascade_result=None,
    )

    enqueued = _drain_queue(queue)
    va_artifacts = [ev for ev in enqueued if isinstance(ev, TaskArtifactUpdateEvent)]
    assert va_artifacts, "应有被转发的 VA artifact 事件"
    # 关键：没有任何事件还带着 VA 自己的 task_id
    assert all(ev.task_id != "va-task-1" for ev in enqueued), \
        "存在未重盖的 VA task_id，会被子 Agent 的 A2A TaskManager 拒绝并关流"
    # 关键：转发的 VA 帧 task_id/context_id 已重盖为本 Agent 的
    for ev in va_artifacts:
        assert ev.task_id == TASK_ID
        assert ev.context_id == CONV_ID
