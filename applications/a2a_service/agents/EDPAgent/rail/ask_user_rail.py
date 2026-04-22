"""
AskUserRail - HITL 中断处理（ask_user 工具）。

对齐需求文档 §4.3（Human-in-the-loop 交互）与 §5.2~§5.5：
  1. 首次调用 ask_user → InterruptRequest 暂停 Agent，Checkpoint 保存
  2. 用户输入到达时 resume → 校验：
       - 终止关键词（config.limits.termination_keywords）→ 终止执行
       - 空或非法 → 记次数，次数达到 max_input_attempts → 终止
       - 合法 → approve，继续执行
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserTool

from .. import state_keys
from ..agent_rule import AgentRuleConfig


class AskUserRail(BaseInterruptRail):
    """拦截 ask_user 工具，配合 Runner Checkpoint 实现跨请求 HITL。"""

    priority = 90

    def __init__(self, config: AgentRuleConfig) -> None:
        super().__init__(tool_names=["ask_user"])
        self.config = config
        self.max_input_attempts = config.limits.max_input_attempts
        self.termination_keywords = config.limits.termination_keywords
        self._tools: list[AskUserTool] = []

    def init(self, agent) -> None:
        """Rail 激活时注册 ask_user 工具，使 LLM 能调用它。"""
        self._tools = [AskUserTool()]
        from openjiuwen.core.runner.runner import Runner
        Runner.resource_mgr.add_tool(self._tools)
        for tool in self._tools:
            agent.ability_manager.add(tool.card)

    def uninit(self, agent) -> None:
        """卸载时清理工具。"""
        for tool in self._tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove(name)
            tool_id = getattr(tool.card, "id", None)
            if tool_id:
                from openjiuwen.core.runner.runner import Runner
                Runner.resource_mgr.remove_tool(tool_id)
        self._tools = []

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: Optional[ToolCall],
        user_input: Optional[str],
        auto_confirm_config: Optional[dict] = None,
    ):
        tool_call_id = tool_call.id if tool_call else ""

        # 首次：user_input 为空 → 抛出 InterruptRequest 暂停
        if user_input is None:
            return self._first_call(ctx, tool_call, tool_call_id)

        # 终止关键词
        if self._is_termination(user_input):
            return self._user_termination(ctx, user_input)

        # 空输入或纯空白
        if not user_input.strip():
            return self._invalid_input(ctx, tool_call_id)

        # 正常输入：清空尝试次数，恢复执行
        self._clear_attempts(ctx, tool_call_id)
        return self.approve(new_args=json.dumps({"response": user_input}))

    # ── internal helpers ─────────────────────────────────────────────

    def _first_call(self, ctx, tool_call, tool_call_id: str):
        question = self._extract_question(ctx)
        interrupt_id = str(uuid.uuid4())
        req = InterruptRequest(
            interrupt_id=interrupt_id,
            message=question,
            context={
                "tool_call_id": tool_call_id,
                "question": question,
            },
        )
        return self.interrupt(req)

    def _user_termination(self, ctx, user_input: str):
        ctx.request_force_finish({
            "type": "user_termination",
            "content": "好的，已取消本次操作",
            "user_input": user_input,
        })
        return self.approve()

    def _invalid_input(self, ctx, tool_call_id: str):
        attempts = self._get_attempts(ctx, tool_call_id) + 1
        self._set_attempts(ctx, tool_call_id, attempts)

        if attempts >= self.max_input_attempts:
            ctx.request_force_finish({
                "type": "max_attempts_exceeded",
                "content": f"连续无效输入达到上限（{self.max_input_attempts}），终止执行",
            })
            return self.approve()

        req = InterruptRequest(
            interrupt_id=str(uuid.uuid4()),
            message="输入无效，请重新输入",
            context={"tool_call_id": tool_call_id, "attempts": attempts},
        )
        return self.interrupt(req)

    def _extract_question(self, ctx) -> str:
        args = getattr(ctx.inputs, "tool_args", "") or ""
        if isinstance(args, str):
            try:
                return json.loads(args).get("question", "请确认")
            except json.JSONDecodeError:
                return "请确认"
        if isinstance(args, dict):
            return args.get("question", "请确认")
        return "请确认"

    def _is_termination(self, user_input: str) -> bool:
        if not user_input:
            return False
        lower = user_input.lower()
        return any(kw.lower() in lower for kw in self.termination_keywords)

    def _get_attempts(self, ctx, tool_call_id: str) -> int:
        d = ctx.session.get_state(state_keys.INPUT_ATTEMPTS) or {}
        return d.get(tool_call_id, 0)

    def _set_attempts(self, ctx, tool_call_id: str, count: int) -> None:
        d = ctx.session.get_state(state_keys.INPUT_ATTEMPTS) or {}
        d[tool_call_id] = count
        ctx.session.update_state({state_keys.INPUT_ATTEMPTS: d})

    def _clear_attempts(self, ctx, tool_call_id: str) -> None:
        d = ctx.session.get_state(state_keys.INPUT_ATTEMPTS) or {}
        if tool_call_id in d:
            del d[tool_call_id]
            ctx.session.update_state({state_keys.INPUT_ATTEMPTS: d})
