# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Agent 事件 → A2A v1.0 事件转换。

映射规则（对齐需求文档 §4.5）：
  顶层主 Agent（final_answer_terminal=True，默认）：final_answer_end / AnswerEvent(final=True)
  → COMPLETED；其余事件 → TaskArtifactUpdateEvent(last_chunk=False)。
  子 Agent（final_answer_terminal=False）：final_answer_end 也走 artifact，终态由
  Executor 在本轮自然结束时补发一次 COMPLETED——避免中途 final_answer_end 被 A2A server
  当作终态而提前关流（详见 agent_event_to_a2a docstring）。
  artifact 的 parts 里携带：
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
    # 并行子 Agent / 多工作流
    SubAgentDispatchRequest, MultiDelegateRequest, SubTaskEvent,
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
    final_answer_terminal: bool = True,
) -> Optional[TaskArtifactUpdateEvent | TaskStatusUpdateEvent]:
    """AgentEvent → A2A 事件。DelegateRequest 返回 None 由 Executor 处理。

    ``final_answer_terminal``（默认 True，顶层主 Agent 路径）：把 FinalAnswerEndEvent /
    AnswerEvent(final=True) 映射为 COMPLETED 终态——顶层由 user_router 抽干队列到 close
    才停，多个 COMPLETED 不会截断，历史前端报文序列不变。
    子 Agent 路径须传 ``False``：子 Agent 经 A2A server 流式回传父侧，A2A server 一旦遇到
    COMPLETED 终态即判定任务结束、关闭流（见 a2a sdk active_task.py 终态处理）。规划型 Agent
    在工具调用之间会发多段 final_answer_end（含首段开场白），若中途就发 COMPLETED，子 Agent
    的真实 cascade 结果（call_versatile / call_multiversatile）将无法回传。传 False 时
    final_answer_end 降级为普通 artifact 透传，真正的终态由 Executor.run_agent 在本轮自然
    结束时统一补发一次 COMPLETED。
    """

    # ── 终止态：TaskStatusUpdateEvent(COMPLETED)（仅顶层 final_answer_terminal=True）──
    if final_answer_terminal and isinstance(event, FinalAnswerEndEvent):
        return _completed(task_id, conv_id, event.content)

    # 兼容旧 AnswerEvent(final=True)
    if final_answer_terminal and isinstance(event, AnswerEvent) and event.final:
        return _completed(task_id, conv_id, event.content)

    # ── 派发请求：Executor 直接处理，不出 SSE ─────────────────────────
    if isinstance(event, (SubAgentDispatchRequest, MultiDelegateRequest)):
        return None

    # ── 级联盖章信封 SubTaskEvent → TaskArtifactUpdateEvent ───────────
    # data 带 type="sub_task" 判别：使上一跳 _drive_sub_agent 能识别为"已盖章"帧而透传，
    # 顶层 user_router._extract_event_meta 据此走 sub_task 信封分支。
    if isinstance(event, SubTaskEvent):
        data = event.model_dump()   # {type:"sub_task", sub_task_path, node_kind, data:<原帧>}
        return _artifact_event(task_id, conv_id, _build_artifact("", data))

    # ── 其余事件统一 TaskArtifactUpdateEvent ──────────────────────────
    event_groups = (
        ConversationStartEvent, ConversationEndEvent,
        ThinkStartEvent, ThinkChunkEvent, ThinkEndEvent,
        TodoListStartEvent, TodoListItemEvent, TodoListEndEvent,
        TodoStartEvent, TodoStatusEvent, TodoEndEvent,
        ToolStartEvent, ToolStatusEvent, ToolEndEvent,
        PlanningExecutionProcessEvent,
        InterruptStartEvent, InterruptEndEvent,
        FinalAnswerStartEvent, SummaryEvent, FinalAnswerChunkEvent, FinalAnswerEndEvent,
        ThoughtEvent, AnswerEvent,
    )
    if isinstance(event, event_groups):
        data = event.model_dump()
        text = str(data.get("content", "") or "")
        return _artifact_event(task_id, conv_id, _build_artifact(text, data))

    # DelegateRequest 等由上层处理
    return None
