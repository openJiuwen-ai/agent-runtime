"""
Agent 事件 → A2A v1.0 事件转换。

转换规则：
  ThoughtEvent    → TaskArtifactUpdateEvent（中间输出）
  AnswerEvent     → TaskArtifactUpdateEvent（非最终）/ TaskStatusUpdateEvent(completed)（最终）
  ToolStartEvent  → TaskArtifactUpdateEvent（工具开始，含 plugin/args）
  ToolEndEvent    → TaskArtifactUpdateEvent（工具结束，含 plugin/data）
  DelegateRequest → None（由 Executor 直接处理）
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    ROLE_AGENT,
    TASK_STATE_COMPLETED,
)
from google.protobuf.struct_pb2 import Struct, Value

from common.events import AgentEvent, AnswerEvent, ThoughtEvent, ToolStartEvent, ToolEndEvent


def _build_data_part(data: dict) -> Part:
    struct = Struct()
    struct.update(data)
    value = Value()
    value.struct_value.CopyFrom(struct)
    part = Part()
    part.data.CopyFrom(value)
    return part


def agent_event_to_a2a(
    event: AgentEvent,
    task_id: str,
    conv_id: str,
) -> Optional[TaskArtifactUpdateEvent | TaskStatusUpdateEvent]:
    if isinstance(event, ThoughtEvent):
        part = Part(text=event.content)
        artifact = Artifact(
            artifact_id=str(uuid.uuid4()),
            parts=[part],
        )
        return TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=conv_id,
            artifact=artifact,
            last_chunk=False,
        )

    if isinstance(event, AnswerEvent):
        part = Part(text=event.content)
        artifact = Artifact(
            artifact_id=str(uuid.uuid4()),
            parts=[part],
        )
        if event.final:
            msg = Message(
                role=ROLE_AGENT,
                message_id=str(uuid.uuid4()),
                parts=[part],
            )
            return TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=conv_id,
                status=TaskStatus(
                    state=TASK_STATE_COMPLETED,
                    message=msg,
                ),
            )
        return TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=conv_id,
            artifact=artifact,
            last_chunk=False,
        )

    if isinstance(event, ToolStartEvent):
        text_part = Part(text=event.content)
        data_part = _build_data_part({
            "event": "tool_start",
            "plugin": event.plugin,
            "args": event.args,
        })
        artifact = Artifact(
            artifact_id=str(uuid.uuid4()),
            parts=[text_part, data_part],
        )
        return TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=conv_id,
            artifact=artifact,
            last_chunk=False,
        )

    if isinstance(event, ToolEndEvent):
        text_part = Part(text=event.content)
        data_part = _build_data_part({
            "event": "tool_end",
            "plugin": event.plugin,
            "data": event.data,
        })
        artifact = Artifact(
            artifact_id=str(uuid.uuid4()),
            parts=[text_part, data_part],
        )
        return TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=conv_id,
            artifact=artifact,
            last_chunk=False,
        )

    return None
