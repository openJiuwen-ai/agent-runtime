# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
EDPAgent 事件协议（与 A2A SDK 完全解耦）。

本模块对齐《动态规划 Agent 综合需求文档》§4.5 的完整事件序列：
  会话：conversation_start / conversation_end
  思考：think_start / think_chunk / think_end
  规划：todolist_start / todolist_item / todolist_end
  任务：todo_start / todo_status / todo_end
  工具：tool_start / tool_status / tool_end
  中断：interrupt_start / interrupt_end
  总结：final_answer_start / final_answer_chunk / final_answer_end

另外保留：
  ThoughtEvent / AnswerEvent  —— 原版兼容事件（agent_adapter 中仍有映射）
  DelegateRequest             —— Executor 专用（VA 委托路径）

所有事件只依赖 pydantic，不引用 a2a.* 模块。
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# ════════════════════════════════════════════════════════════════════
# 会话事件
# ════════════════════════════════════════════════════════════════════


class ConversationStartEvent(BaseModel):
    """对话开启。每次北向请求开始时发出一次。"""
    type: Literal["conversation_start"] = "conversation_start"
    content: str = ""


class ConversationEndEvent(BaseModel):
    """对话结束。整个处理流程结束时发出一次。"""
    type: Literal["conversation_end"] = "conversation_end"
    content: str = ""


# ════════════════════════════════════════════════════════════════════
# 思考事件（从 llm_reasoning 流中分段）
# ════════════════════════════════════════════════════════════════════


class ThinkStartEvent(BaseModel):
    """LLM 思考开始（每轮 ReAct 的第一个 reasoning chunk）。"""
    type: Literal["think_start"] = "think_start"
    content: str = ""
    display: bool | None = Field(
        default=None,
        description="true=前端开始拼接, false=前端停止拼接, None=不指定(向后兼容)"
    )


class ThinkChunkEvent(BaseModel):
    """LLM 思考流式片段。"""
    type: Literal["think_chunk"] = "think_chunk"
    content: str
    display: bool | None = Field(
        default=None,
        description="true=前端继续拼接, false=前端停止拼接, None=不指定(向后兼容)"
    )


class ThinkEndEvent(BaseModel):
    """LLM 思考结束（reasoning 流结束或切换为 tool_call / answer）。"""
    type: Literal["think_end"] = "think_end"
    content: str = ""
    display: bool | None = Field(
        default=None,
        description="true=前端停止拼接并展示, false=前端丢弃拼接内容, None=不指定(向后兼容)"
    )


# ════════════════════════════════════════════════════════════════════
# 规划事件（Todolist，由 LLM 在 thought 中按约定 JSON 输出）
# ════════════════════════════════════════════════════════════════════


class TodoListStartEvent(BaseModel):
    """Todolist 生成开始。"""
    type: Literal["todolist_start"] = "todolist_start"
    content: str = ""


class TodoListItemEvent(BaseModel):
    """Todolist 中的单个任务条目。"""
    type: Literal["todolist_item"] = "todolist_item"
    id: int | str
    title: str
    status: Literal["pending", "in_progress", "done", "failed", "cancelled"] = "pending"
    content: str = ""


class TodoListEndEvent(BaseModel):
    """Todolist 生成完成。"""
    type: Literal["todolist_end"] = "todolist_end"
    count: int = 0
    content: str = ""


# ════════════════════════════════════════════════════════════════════
# 任务事件（单个 todo 状态变更，由 LLM 在 thought 中按约定 JSON 输出）
# ════════════════════════════════════════════════════════════════════


class TodoStartEvent(BaseModel):
    """单个 Todo 开始执行。"""
    type: Literal["todo_start"] = "todo_start"
    id: int | str
    title: str = ""
    content: str = ""


class TodoStatusEvent(BaseModel):
    """单个 Todo 状态变更（执行中）。"""
    type: Literal["todo_status"] = "todo_status"
    id: int | str
    status: Literal["pending", "in_progress", "done", "failed"]
    content: str = ""


class TodoEndEvent(BaseModel):
    """单个 Todo 执行结束。"""
    type: Literal["todo_end"] = "todo_end"
    id: int | str
    status: Literal["done", "failed"] = "done"
    content: str = ""


# ════════════════════════════════════════════════════════════════════
# 工具事件（从 Runner 的 tool_start / tool_end 映射）
# ════════════════════════════════════════════════════════════════════


class ToolStartEvent(BaseModel):
    """工具调用开始。"""
    type: Literal["tool_start"] = "tool_start"
    content: str = ""
    plugin: str = Field(default="", description="工具名")
    args: dict[str, Any] = Field(default_factory=dict, description="工具入参")


class ToolStatusEvent(BaseModel):
    """工具调用进行中（可选，用于长时任务的进度）。"""
    type: Literal["tool_status"] = "tool_status"
    plugin: str = ""
    content: str = ""
    progress: Optional[float] = None  # 0.0 - 1.0


class ToolEndEvent(BaseModel):
    """工具调用结束。"""
    type: Literal["tool_end"] = "tool_end"
    content: str = ""
    plugin: str = Field(default="", description="工具名")
    data: dict[str, Any] = Field(default_factory=dict, description="工具返回数据")


# ════════════════════════════════════════════════════════════════════
# 执行轨迹事件（由 Executor 在 step 边界发射）
# ════════════════════════════════════════════════════════════════════


class PlanningExecutionProcessEvent(BaseModel):
    """执行轨迹（step 边界标记）。

    形如 "[执行轨迹] 正在执行步骤N: <desc> (tool=<tool_name>)" 的字符串在 content
    里提供给前端做"当前卡在哪一步"的展示。

    此事件在北向包装里**独有 error_code: ""** 字段（对齐抓包）。
    """
    type: Literal["planning_execution_process"] = "planning_execution_process"
    content: str = ""


# ════════════════════════════════════════════════════════════════════
# 中断事件（HITL）
# ════════════════════════════════════════════════════════════════════


class InterruptStartEvent(BaseModel):
    """等待用户输入。"""
    type: Literal["interrupt_start"] = "interrupt_start"
    interrupt_id: str
    content: str = Field(default="", description="提示话术")
    context: dict[str, Any] = Field(default_factory=dict)


class InterruptEndEvent(BaseModel):
    """中断已恢复（用户输入被接受）。"""
    type: Literal["interrupt_end"] = "interrupt_end"
    interrupt_id: str = ""
    content: str = Field(default="", description="回显确认信息")


# ════════════════════════════════════════════════════════════════════
# 总结事件（从 llm_output / answer 流分段）
# ════════════════════════════════════════════════════════════════════


class FinalAnswerStartEvent(BaseModel):
    """最终回答开始。"""
    type: Literal["final_answer_start"] = "final_answer_start"
    content: str = ""


class SummaryEvent(BaseModel):
    """流式总结片段（按 LLM token 切片逐段发出）。

    规范定义（feat-north-api-sse.md §4.5.9 / north-api-response-format.md §4.9）：
      - `summary`：流式通道，承担"边想边说"的 UI 展示
      - `final_answer_chunk`：**一次性全量**通道，承担"最终权威文本"
    两者并存，互补不冲突。前端流式展示用 SummaryEvent，存档/回显用
    FinalAnswerChunkEvent。
    """
    type: Literal["summary"] = "summary"
    content: str


class FinalAnswerChunkEvent(BaseModel):
    """最终回答的**一次性全量**文本帧。

    注意：抓包与规范一致 —— 这一帧并非流式片段，而是在所有 `summary` 流式
    片段结束后，以单帧形式发送完整版（供校对/回显/存档使用）。

    流式片段请使用 SummaryEvent。
    """
    type: Literal["final_answer_chunk"] = "final_answer_chunk"
    content: str


class FinalAnswerEndEvent(BaseModel):
    """最终回答结束。"""
    type: Literal["final_answer_end"] = "final_answer_end"
    content: str = ""


# ════════════════════════════════════════════════════════════════════
# Executor 专用（非北向事件，不出流）
# ════════════════════════════════════════════════════════════════════


class DelegateRequest(BaseModel):
    """
    Agent 需要外部 Agent 处理子任务时 yield 的委托对象。

    协议约定：Orchestrator 收到此对象后：
      1. 调用 target_agent 完成子任务
      2. 获得 workflow_result（dict）
      3. 以 cascade_result=workflow_result 再次调用 agent_stream()
    """
    type: Literal["delegate"] = "delegate"
    intent: str = Field(description="意图标识")
    target_agent: str | None = None
    task_description: str = Field(description="自然语言任务描述")


# ════════════════════════════════════════════════════════════════════
# 兼容事件（保留旧版 API，逐步废弃）
# ════════════════════════════════════════════════════════════════════


class ThoughtEvent(BaseModel):
    """[兼容] 旧版合并的 thought 事件，建议改用 Think* 系列。"""
    type: Literal["thought"] = "thought"
    content: str


class AnswerEvent(BaseModel):
    """[兼容] 旧版合并的 answer 事件，建议改用 FinalAnswer* 系列。"""
    type: Literal["answer"] = "answer"
    content: str
    final: bool = False


# ════════════════════════════════════════════════════════════════════
# 统一 Union 类型
# ════════════════════════════════════════════════════════════════════


AgentEvent = Union[
    # 会话
    ConversationStartEvent,
    ConversationEndEvent,
    # 思考
    ThinkStartEvent,
    ThinkChunkEvent,
    ThinkEndEvent,
    # 规划
    TodoListStartEvent,
    TodoListItemEvent,
    TodoListEndEvent,
    # 任务
    TodoStartEvent,
    TodoStatusEvent,
    TodoEndEvent,
    # 工具
    ToolStartEvent,
    ToolStatusEvent,
    ToolEndEvent,
    # 执行轨迹
    PlanningExecutionProcessEvent,
    # 中断
    InterruptStartEvent,
    InterruptEndEvent,
    # 总结
    FinalAnswerStartEvent,
    SummaryEvent,
    FinalAnswerChunkEvent,
    FinalAnswerEndEvent,
    # Executor
    DelegateRequest,
    # 兼容
    ThoughtEvent,
    AnswerEvent,
]


# 事件 type → 事件类（给 agent_adapter / user_router 用）
EVENT_TYPE_MAP = {
    # 会话
    "conversation_start": ConversationStartEvent,
    "conversation_end": ConversationEndEvent,
    # 思考
    "think_start": ThinkStartEvent,
    "think_chunk": ThinkChunkEvent,
    "think_end": ThinkEndEvent,
    # 规划
    "todolist_start": TodoListStartEvent,
    "todolist_item": TodoListItemEvent,
    "todolist_end": TodoListEndEvent,
    # 任务
    "todo_start": TodoStartEvent,
    "todo_status": TodoStatusEvent,
    "todo_end": TodoEndEvent,
    # 工具
    "tool_start": ToolStartEvent,
    "tool_status": ToolStatusEvent,
    "tool_end": ToolEndEvent,
    # 执行轨迹
    "planning_execution_process": PlanningExecutionProcessEvent,
    # 中断
    "interrupt_start": InterruptStartEvent,
    "interrupt_end": InterruptEndEvent,
    # 总结
    "final_answer_start": FinalAnswerStartEvent,
    "summary": SummaryEvent,
    "final_answer_chunk": FinalAnswerChunkEvent,
    "final_answer_end": FinalAnswerEndEvent,
    # 兼容
    "thought": ThoughtEvent,
    "answer": AnswerEvent,
}
