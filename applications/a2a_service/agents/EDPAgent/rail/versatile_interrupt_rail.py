"""
VersatileInterruptRail：拦截业务工具调用，通过 DelegateRequest 委托给 Orchestrator。

当前仅保留 call_versatile 一条业务调用链：
    - 首次拦截时记录 pending_delegate + pending_tool_context
    - Cascade 续轮时从 workflow_result / End 节点提取 business_data
    - 如配置了 sys_operation_id，则执行沙箱归一化脚本
    - 如未配置 sys_operation_id 或脚本为空，则降级透传 business_data

设计原则：
  - 零 A2A 依赖：不引用 EventQueue、A2AClient 等任何 A2A 对象
  - 不主动调用外部 Agent / 工作流：只记录委托意图，由 Orchestrator 执行
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

# 脚本路径基准：agents/EDPAgent/skills/
_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class VersatileInterruptRail(BaseInterruptRail):
    """call_versatile 的委托 Rail。"""

    def __init__(self, sys_operation_id: Optional[str] = None) -> None:
        super().__init__(tool_names=["call_versatile"])
        self._sys_operation_id = sys_operation_id

    async def resolve_interrupt(
        self,
        ctx,
        tool_call,
        user_input,
        auto_confirm_config=None,
    ):
        tool_args = ctx.inputs.tool_args or {}
        tool_name = tool_call.name if hasattr(tool_call, "name") else None
        tool_args = self._normalize_tool_args(tool_args, tool_name)

        cascade_result = ctx.session.get_state("cascade_result")
        if cascade_result is not None:
            ctx.session.update_state({"cascade_result": None})
            return await self._handle_cascade_resume(ctx, tool_name, tool_args, cascade_result)

        delegate_info = self._build_delegate(tool_name, tool_args)

        logger.info(
            f"[VersatileInterruptRail] 拦截 {tool_name}："
            f"intent={delegate_info['intent']}, desc={delegate_info['task_description']!r:.60}"
        )

        update_payload = {
            "pending_delegate": {
                "intent": delegate_info["intent"],
                "task_description": delegate_info["task_description"],
            }
        }
        if tool_name == "call_versatile":
            update_payload["pending_tool_context"] = {
                "tool_name": tool_name,
                "tool_args": tool_args,
            }

        ctx.session.update_state(update_payload)

        return self.interrupt(
            InterruptRequest(
                message=f"执行{delegate_info['intent']}，等待 Orchestrator Cascade 续轮"
            )
        )

    async def _handle_cascade_resume(self, ctx, tool_name: str, tool_args: dict, cascade_result):
        tool_context = ctx.session.get_state("pending_tool_context") or {}
        ctx.session.update_state({"pending_tool_context": None})
        tool_args = tool_context.get("tool_args", tool_args) or {}

        business_data = self._extract_business_data(cascade_result)
        command = tool_args.get("query_response_analysis_scripts", "")
        skill_input = self._build_skill_input(tool_args, business_data)
        normalized = await self._sandbox_normalize(command, skill_input, business_data)

        logger.info(
            f"[VersatileInterruptRail] Cascade 续轮：command={command!r}, "
            f"result_keys={list(normalized.keys()) if isinstance(normalized, dict) else type(normalized)}"
        )
        return self.reject(tool_result=normalized)

    @staticmethod
    def _normalize_tool_args(tool_args, tool_name: Optional[str]) -> dict:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    @staticmethod
    def _build_skill_input(tool_args: dict, business_data: dict) -> dict:
        return {
            "query_intent": tool_args.get("query_intent", ""),
            "query_description": tool_args.get("query_description", ""),
            "business_data": business_data,
        }

    async def _sandbox_normalize(
        self, command: str, skill_input: dict, fallback: dict,
    ):
        if not command:
            return fallback

        if not self._sys_operation_id:
            logger.warning(
                "[VersatileInterruptRail] 未配置 sys_operation_id，跳过沙箱归一化：command={!r}",
                command,
            )
            return fallback

        from openjiuwen.core.runner import Runner

        sys_op = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        if sys_op is None:
            logger.warning(
                "[VersatileInterruptRail] 未找到 SysOperationCard，跳过沙箱归一化：sys_operation_id={!r}",
                self._sys_operation_id,
            )
            return fallback

        exec_result = await sys_op.shell().execute_cmd(
            command=f"cd {_SKILLS_DIR} && {command}",
            timeout=60,
            environment={"SKILL_INPUT": json.dumps(skill_input, ensure_ascii=False)},
        )

        stdout = getattr(getattr(exec_result, "data", None), "stdout", "") or ""
        stderr = getattr(getattr(exec_result, "data", None), "stderr", "") or ""
        exit_code = getattr(getattr(exec_result, "data", None), "exit_code", None)

        try:
            return json.loads(stdout.strip())
        except (json.JSONDecodeError, AttributeError) as e:
            logger.error(
                "[VersatileInterruptRail] 沙箱脚本输出解析失败：{}，exit_code={}, stdout={!r}, stderr={!r}",
                e,
                exit_code,
                stdout[:500],
                stderr[:500],
            )
            return fallback

    @staticmethod
    def _extract_business_data(cascade_result) -> dict:
        if not isinstance(cascade_result, dict):
            return {}

        workflow_result = cascade_result.get("workflow_result")
        if workflow_result is not None:
            if isinstance(workflow_result, dict):
                return workflow_result
            if isinstance(workflow_result, str):
                try:
                    parsed = json.loads(workflow_result)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    pass
                return {"workflow_result": workflow_result}
            return cascade_result

        return {
            key: value
            for key, value in cascade_result.items()
            if key not in ("node_type", "node_name")
        }

    @staticmethod
    def _build_delegate(tool_name: Optional[str], tool_args: dict) -> dict:
        return {
            "intent": tool_args.get("query_intent", ""),
            "task_description": tool_args.get("query_description", ""),
        }
