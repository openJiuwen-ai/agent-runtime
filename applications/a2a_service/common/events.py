"""
DPA Agent 事件协议（与 A2A 完全解耦）。

DPA 通过 agent_stream() 生成器 yield 以下三种事件：
  - ThoughtEvent:      LLM 思考过程（流式中间输出）
  - AnswerEvent:       最终回答
  - DelegateRequest:  需要外部 Agent 处理子任务时的委托请求

整个模块只依赖 pydantic，无任何 A2A SDK 引用。
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


class ThoughtEvent(BaseModel):
    """LLM 思考过程（流式中间输出）。"""

    type: Literal["thought"] = "thought"
    content: str


class AnswerEvent(BaseModel):
    """最终回答。final=True 时 agent_stream 即将结束。"""

    type: Literal["answer"] = "answer"
    content: str
    final: bool = False


class DelegateRequest(BaseModel):
    """
    DPA 需要外部 Agent 处理子任务时 yield 的委托对象。

    协议约定：
      调用方（Orchestrator）收到此对象后，必须：
        1. 调用 target_agent 完成子任务（如果有）
        2. 获得 workflow_result（dict）
        3. 以 cascade_result=workflow_result 再次调用 agent_stream()
      DPA 的 Runner Checkpoint 已保存，续轮时从中断点恢复。

    强格式保证：
      - Pydantic 模型，字段类型明确
      - target_agent 限制为注册在 agent_runtime 中的 Agent ID（可选）
    """

    type: Literal["delegate"] = "delegate"
    intent: str = Field(description="意图标识，例如 '查余额'、'转账'")
    target_agent: str | None = Field(None, description="目标 Agent 标识符，例如 'workflow_adapter'")
    task_description: str = Field(description="自然语言任务描述")


# DPA agent_stream 的完整输出类型签名
AgentEvent = Union[ThoughtEvent, AnswerEvent, DelegateRequest]
