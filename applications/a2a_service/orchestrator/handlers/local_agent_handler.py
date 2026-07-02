from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from ..route.normalized_event import NormalizedEvent, RouteTarget
from ..state import TaskStateManager


class LocalAgentHandler:
    """Local Agent 目标处理器

    职责：
    - 处理 request 类型事件：首轮创建 Task + 启动 agent_stream
    - 处理 completed 类型事件：finalize_completed

    注意：
    - INPUT_REQUIRED 续轮逻辑已由 RequesterSourceStrategy 路由到 remote_agent 处理，
      不再在此 handler 中判断
    - continue 事件由 RemoteAgentHandler 处理，不会路由到此处
    """

    def __init__(self, state_manager: TaskStateManager, local_agent_name: str):
        self._state_manager = state_manager
        self._local_agent_name = local_agent_name

    async def handle(
        self,
        event: NormalizedEvent,
        target: RouteTarget,
        context: Dict[str, Any],
    ) -> None:
        event_type = event.type
        task_id = context.get("task_id", "")
        conv_id = context.get("conv_id", "")
        call_context = context.get("call_context")
        current_task = context.get("current_task")
        executor = context.get("executor")
        turn_ctx = context.get("turn_ctx")
        query = context.get("query", "")
        original_body = context.get("original_body", {})
        step_counter = context.get("step_counter")

        if event_type == "request":
            if current_task is None:
                await self._state_manager.create_task(
                    task_id, conv_id, call_context=call_context,
                    source_agent=self._local_agent_name,
                )
                logger.debug(
                    f"[LocalAgentHandler] 创建 Task：task={task_id}, conv={conv_id}, source_agent={self._local_agent_name}"
                )
            if executor is not None and turn_ctx is not None:
                await executor.run_agent(
                    turn_ctx,
                    query=query,
                    original_body=original_body,
                    cascade_result=None,
                    step_counter=step_counter or [0],
                )
            return

        # NOTE: completed 分支当前为预留逻辑，尚未有业务路径触发。
        # 触发条件：RemoteAgentHandler 将 VA 返回的终态事件（如 COMPLETED）
        # 归一化为 NormalizedEvent(source=remote_agent, type=completed) 后走 dispatch 路由，
        # 由 RemoteAgentSourceStrategy 将 CONTROL_COMPLETED 帧路由到 local_agent 目标。
        # 目前 VA 事件仍由 RemoteAgentHandler 直接 enqueue_event，未走 dispatch，
        # 因此该分支暂不可达。待 VA 事件统一走 dispatch 路由后激活。
        if event_type == "completed":
            await self._state_manager.finalize_completed(task_id, call_context)
            logger.debug(
                f"[LocalAgentHandler] Task COMPLETED: task={task_id}, conv={conv_id}"
            )
            return
