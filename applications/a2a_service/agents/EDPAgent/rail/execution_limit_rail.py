"""
ExecutionLimitRail - 按工具名的执行次数计数与上限控制。

规则 5（需求文档 §4.2）：按工具名配置上限（如 call_versatile: 10）。

同时承担在 Runner 的流里补发 tool_start / tool_end 事件的职责
（Runner 自身发的事件类型名就是 tool_start / tool_end，本 Rail 用
同名 OutputSchema 做话术层覆盖/补齐）。
"""
from __future__ import annotations

import json

from loguru import logger
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentRail, AgentCallbackContext

from .. import state_keys
from ..agent_rule import AgentRuleConfig


DEFAULT_LIMIT = 5


class ExecutionLimitRail(AgentRail):
    """按工具名限流并补齐 tool_start/tool_end 事件。"""

    priority = 40

    def __init__(self, config: AgentRuleConfig) -> None:
        self.config = config
        self.task_limits = config.limits.tasks
        self.default_limit = DEFAULT_LIMIT
        self.scripts = config.scripts

    def _get_limit(self, tool_name: str) -> int:
        return self.task_limits.get(tool_name, self.default_limit)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = getattr(ctx.inputs, "tool_name", "") or ""
        tool_args = getattr(ctx.inputs, "tool_args", {}) or {}
        counts = ctx.session.get_state(state_keys.EXEC_COUNTS) or {}

        # 兼容字符串形式的 tool_args
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args) if tool_args else {}
            except json.JSONDecodeError:
                tool_args = {}

        current = counts.get(tool_name, 0)
        limit = self._get_limit(tool_name)

        if current >= limit:
            ctx.request_force_finish({
                "type": "execution_limit_exceeded",
                "content": f"工具 {tool_name} 已达到执行次数限制（{limit}），终止执行",
                "tool_name": tool_name,
                "count": current,
            })
            return

        counts[tool_name] = current + 1
        ctx.session.update_state({state_keys.EXEC_COUNTS: counts})

        # 补发 tool_start 事件（话术 + 入参）
        content = self.scripts.tool_start.format(tool_name=tool_name)
        await ctx.session.write_stream(OutputSchema(
            type="tool_start",
            index=0,
            payload={
                "content": content,
                "plugin": tool_name,
                "args": tool_args,
            },
        ))
        logger.info(f"[ExecutionLimitRail] tool_start: {tool_name}")

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = getattr(ctx.inputs, "tool_name", "") or ""
        tool_result = getattr(ctx.inputs, "tool_result", None)

        # 提取返回 data
        result_data: dict = {}
        if tool_result:
            if isinstance(tool_result, dict):
                result_data = tool_result.get("data", tool_result)
            elif hasattr(tool_result, "data"):
                result_data = tool_result.data if isinstance(tool_result.data, dict) else {}
            elif hasattr(tool_result, "model_dump"):
                dumped = tool_result.model_dump()
                result_data = dumped.get("data", dumped)

        content = self.scripts.tool_end.format(tool_name=tool_name)
        await ctx.session.write_stream(OutputSchema(
            type="tool_end",
            index=0,
            payload={
                "content": content,
                "plugin": tool_name,
                "data": result_data,
            },
        ))
