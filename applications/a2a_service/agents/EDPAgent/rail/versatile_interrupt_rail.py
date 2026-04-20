"""
VersatileInterruptRail：拦截 query_balance 和 transfer 工具调用，通过 session state 传递委托信息。

设计原则：
  - 零 A2A 依赖：不引用 EventQueue、A2AClient 等任何 A2A 对象
  - 不主动调用外部 Agent：只记录委托意图，由 Orchestrator 执行
  - Cascade 续轮：从 session state 读取 cascade_result，直接 reject(tool_result=result)

数据流：
  Rail.resolve_interrupt()
    → session.update_state({"pending_delegate": {...}})   # 首次委托
    → interrupt()                                          # Runner 保存 Checkpoint
  agent_stream() 在 run_stream/resume 结束后
    → session.get_state("pending_delegate")
    → yield DelegateRequest(...)                           # 传递给 Orchestrator
"""
from __future__ import annotations

from loguru import logger
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail


class VersatileInterruptRail(BaseInterruptRail):
    """
    拦截 query_balance 和 transfer 工具调用的 Rail。

    两种执行路径：
      1. 首次拦截（cascade_result 不存在）：
         - 读取工具参数 task_description
         - 根据工具名设置 intent（query_balance → "查余额"，transfer → "转账"）
         - 写入 pending_delegate 到 session state
         - 调用 interrupt() → Runner 保存 Checkpoint → stream 生成器结束
         - agent_stream() 函数末尾读取 pending_delegate → yield DelegateRequest

      2. Cascade 续轮（cascade_result 存在）：
         - Orchestrator 在 agent_stream() 调用前注入 cascade_result 到 session state
         - Rail 读取并消费 cascade_result
         - reject(tool_result=cascade_result) → Runner 继续，LLM 收到工具返回值
    """

    def __init__(self) -> None:
        super().__init__(tool_names=["query_balance", "transfer"])

    async def resolve_interrupt(
        self,
        ctx,
        tool_call,
        user_input,
        auto_confirm_config=None,
    ):
        tool_args = ctx.inputs.tool_args or {}
        tool_name = tool_call.name if hasattr(tool_call, "name") else None

        # 兼容处理：如果 tool_args 是字符串，尝试解析
        if isinstance(tool_args, str):
            import json
            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {"task_description": tool_args}

        # ── Cascade 续轮快捷路径 ──────────────────────────────────────────
        # Orchestrator 通过 agent_stream(cascade_result=...) 注入结果，
        # agent.py 在续轮时调用 session.update_state({"cascade_result": ...})
        cascade_result = ctx.session.get_state("cascade_result")
        if cascade_result is not None:
            # cascade_result = "余额查询结果：余额为100元"
            ctx.session.update_state({"cascade_result": None})  # 消费
            logger.info(
                f"[VersatileInterruptRail] Cascade 续轮：注入 workflow_result 给 LLM，"
                f"cascade_result={cascade_result}"
            )
            return self.reject(tool_result=cascade_result)

        # ── 首次拦截：记录委托意图 ────────────────────────────────────────
        task_description = tool_args.get("task_description", "")

        # 根据工具名设置 intent
        intent = ""
        if tool_name == "query_balance":
            intent = "查询账户余额"
        elif tool_name == "transfer":
            intent = "快速转账"

        logger.info(
            f"[VersatileInterruptRail] 拦截工具调用："
            f"tool={tool_name}, intent={intent}, desc={task_description!r:.60}"
        )

        ctx.session.update_state(
            {
                "pending_delegate": {
                    "intent": intent,
                    "task_description": task_description,
                }
            }
        )

        logger.info(
            f"[VersatileInterruptRail] 委托意图已记录："
            f"intent={intent}, desc={task_description!r:.60}"
        )

        # interrupt() → Runner 保存 Checkpoint → agent.stream() 生成器结束
        # agent_stream() 函数末尾会读取 pending_delegate 并 yield DelegateRequest
        return self.interrupt(
            InterruptRequest(message=f"执行{intent}，等待 Orchestrator Cascade 续轮")
        )
