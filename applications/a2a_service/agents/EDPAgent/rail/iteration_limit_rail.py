"""
IterationLimitRail - 总迭代次数计数与上限控制。

规则 4（需求文档 §4.2）：默认 30 次，以 tool 调用次数计数。

在 after_tool_call 钩子自增计数；到达上限时调用 request_force_finish
触发 Agent 终止，并在流里 yield 一段"超限"话术。
"""
from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentRail, AgentCallbackContext

from .. import state_keys
from ..agent_rule import AgentRuleConfig


class IterationLimitRail(AgentRail):
    """限制一次 session 内的 tool 调用次数。"""

    priority = 40

    def __init__(self, config: AgentRuleConfig) -> None:
        self.config = config
        self.max_iterations = config.limits.max_iterations

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        current = ctx.session.get_state(state_keys.ITER_COUNT) or 0
        new_count = current + 1
        ctx.session.update_state({state_keys.ITER_COUNT: new_count})

        if new_count >= self.max_iterations:
            ctx.request_force_finish({
                "type": "iteration_limit_exceeded",
                "content": f"已达到最大迭代次数上限（{self.max_iterations}），终止执行",
                "iteration_count": new_count,
            })
