"""
Agent 事件 → A2A v1.0 事件转换。

转换规则：
  ThoughtEvent    → TaskArtifactUpdateEvent（中间输出）
  AnswerEvent     → TaskArtifactUpdateEvent（非最终）/ TaskStatusUpdateEvent(completed)（最终）
  DelegateRequest → None（由 Executor 直接处理）
"""
from __future__ import annotations

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

from common.events import AgentEvent, AnswerEvent, ThoughtEvent


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

    return None
