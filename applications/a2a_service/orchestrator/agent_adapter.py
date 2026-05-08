# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Agent 事件 → A2A v1.0 事件转换。

映射规则（对齐需求文档 §4.5）：
  除 final_answer_end / AnswerEvent(final=True) 触发 COMPLETED 外，
  所有事件统一封装为 TaskArtifactUpdateEvent(last_chunk=False)，
  在 parts 里携带：
    - Part(text=<content>)      可读文本（若有）
    - Part(data=<event fields>) 结构化数据，客户端按 data.type 分派

  DelegateRequest → None（Executor 直接处理）
"""
from __future__ import annotations

import uuid
from typing import Optional

from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    ROLE_AGENT,
    TASK_STATE_COMPLETED,
)
from google.protobuf.struct_pb2 import Struct, Value

from common.events import (
    AgentEvent,
    # 会话
    ConversationStartEvent, ConversationEndEvent,
    # 思考
    ThinkStartEvent, ThinkChunkEvent, ThinkEndEvent,
    # 规划
    TodoListStartEvent, TodoListItemEvent, TodoListEndEvent,
    # 任务
    TodoStartEvent, TodoStatusEvent, TodoEndEvent,
    # 工具
    ToolStartEvent, ToolStatusEvent, ToolEndEvent,
    # 执行轨迹
    PlanningExecutionProcessEvent,
    # 中断
    InterruptStartEvent, InterruptEndEvent,
    # 总结
    FinalAnswerStartEvent, SummaryEvent, FinalAnswerChunkEvent, FinalAnswerEndEvent,
    # 兼容
    ThoughtEvent, AnswerEvent,
)


def _build_data_part(data: dict) -> Part:
    struct = Struct()
    # Struct 要求所有值必须是 JSON 可序列化的简单类型；pydantic model_dump 已经做过
    struct.update(data)
    value = Value()
    value.struct_value.CopyFrom(struct)
    part = Part()
    part.data.CopyFrom(value)
    return part


def _build_artifact(text: str, data: dict) -> Artifact:
    parts = []
    if text:
        parts.append(Part(text=text))
    parts.append(_build_data_part(data))
    return Artifact(artifact_id=str(uuid.uuid4()), parts=parts)


def _artifact_event(task_id: str, conv_id: str, artifact: Artifact) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=conv_id,
        artifact=artifact,
        last_chunk=False,
    )


def _completed(task_id: str, conv_id: str, content: str) -> TaskStatusUpdateEvent:
    part = Part(text=content)
    msg = Message(role=ROLE_AGENT, message_id=str(uuid.uuid4()), parts=[part])
    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=conv_id,
        status=TaskStatus(state=TASK_STATE_COMPLETED, message=msg),
    )


def agent_event_to_a2a(
    event: AgentEvent,
    task_id: str,
    conv_id: str,
) -> Optional[TaskArtifactUpdateEvent | TaskStatusUpdateEvent]:
    """AgentEvent → A2A 事件。DelegateRequest 返回 None 由 Executor 处理。"""

    # ── 终止态：TaskStatusUpdateEvent(COMPLETED) ─────────────────────
    if isinstance(event, FinalAnswerEndEvent):
        return _completed(task_id, conv_id, event.content)

    # 兼容旧 AnswerEvent(final=True)
    if isinstance(event, AnswerEvent) and event.final:
        return _completed(task_id, conv_id, event.content)

    # ── 其余事件统一 TaskArtifactUpdateEvent ──────────────────────────
    event_groups = (
        ConversationStartEvent, ConversationEndEvent,
        ThinkStartEvent, ThinkChunkEvent, ThinkEndEvent,
        TodoListStartEvent, TodoListItemEvent, TodoListEndEvent,
        TodoStatusEvent, TodoEndEvent,
        ToolStartEvent, ToolStatusEvent, ToolEndEvent,
        PlanningExecutionProcessEvent,
        InterruptStartEvent, InterruptEndEvent,
        FinalAnswerStartEvent, SummaryEvent, FinalAnswerChunkEvent,
        ThoughtEvent, AnswerEvent,
    )
    if isinstance(event, event_groups):
        data = event.model_dump()
        text = str(data.get("content", "") or "")
        return _artifact_event(task_id, conv_id, _build_artifact(text, data))

    # DelegateRequest 等由上层处理
    return None
