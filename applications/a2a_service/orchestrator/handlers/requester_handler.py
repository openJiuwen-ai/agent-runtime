from __future__ import annotations

from typing import Any, Dict

from a2a.types.a2a_pb2 import (
    TaskStatus,
    TaskStatusUpdateEvent,
    TASK_STATE_INPUT_REQUIRED,
)
from loguru import logger

from channels.dict_to_a2a import dict_to_a2a
from ..route.normalized_event import NormalizedEvent, RouteTarget
from ..state import InputRequiredState, TaskStateManager


def _raw_data(raw_event: Any) -> dict[str, Any]:
    if isinstance(raw_event, dict):
        data = raw_event.get("data", {})
        return data if isinstance(data, dict) else {}
    return {}


def _agent_dict_to_a2a(raw_event: Any, task_id: str, conv_id: str):
    if not isinstance(raw_event, dict):
        return None

    event_type = raw_event.get("type")
    data = raw_event.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if event_type == "delegate":
        return None

    return dict_to_a2a({"type": event_type, "data": data}, task_id, conv_id)


class RequesterHandler:
    """Requester 目标处理器（转发事件到请求端，请求端可以是 Channel 或请求端 Agent）

    处理来自 local_agent 的归一化事件：
    - tool_start：step 边界发射 + planning_execution_process + 原始事件 a2a 入队
    - 其他事件（think_start / text_chunk / tool_end 等）：raw_event a2a 转换 + 入队
    - input_required：挂起任务状态
    - failed：标记任务失败
    """

    def __init__(self, state_manager: TaskStateManager):
        self._state_manager = state_manager

    async def handle(
        self,
        event: NormalizedEvent,
        target: RouteTarget,
        context: Dict[str, Any],
    ) -> None:
        event_type = event.type
        event_data = event.data
        event_queue = context.get("event_queue")
        task_id = context.get("task_id", "")
        conv_id = context.get("conv_id", "")
        call_context = context.get("call_context")
        step_counter = context.get("step_counter")

        if event_type == "tool_start":
            await self._handle_tool_start(
                event_data, task_id, conv_id, event_queue, step_counter,
            )
            return

        if event_type == "input_required":
            await self._handle_input_required(
                event_data, task_id, conv_id, call_context, event_queue
            )
            return

        if event_type == "failed":
            await self._handle_failed(event_data, task_id, conv_id, call_context)
            return

        await self._handle_raw_event(event_data, task_id, conv_id, event_queue)

    async def _handle_tool_start(
        self,
        event_data: Dict[str, Any],
        task_id: str,
        conv_id: str,
        event_queue: Any,
        step_counter: Any,
    ) -> None:
        raw_event = event_data.get("raw_event")
        if raw_event is None:
            return

        data = _raw_data(raw_event)
        plugin = str(data.get("plugin") or "")
        desc = str(data.get("content") or plugin)

        if step_counter is not None:
            step_counter[0] += 1

        planning_content = (
            f"[执行轨迹] 正在执行步骤{step_counter[0]}: {desc} "
            f"(tool={plugin})"
        )
        planning = {
            "type": "planning_execution_process",
            "data": {"content": planning_content},
        }
        planning_a2a = dict_to_a2a(planning, task_id, conv_id)
        if planning_a2a is not None and event_queue is not None:
            await event_queue.enqueue_event(planning_a2a)

        logger.info(
            f"[RequesterHandler] step 边界: 步骤{step_counter[0]} "
            f"(tool={plugin}, desc={desc!r:.60})"
        )

        a2a_event = _agent_dict_to_a2a(raw_event, task_id, conv_id)
        if a2a_event is not None and event_queue is not None:
            await event_queue.enqueue_event(a2a_event)

    async def _handle_raw_event(
        self,
        event_data: Dict[str, Any],
        task_id: str,
        conv_id: str,
        event_queue: Any,
    ) -> None:
        raw_event = event_data.get("raw_event")
        if raw_event is None:
            return
        a2a_event = _agent_dict_to_a2a(raw_event, task_id, conv_id)
        if a2a_event is not None and event_queue is not None:
            await event_queue.enqueue_event(a2a_event)

    async def _handle_input_required(
        self,
        event_data: Dict[str, Any],
        task_id: str,
        conv_id: str,
        call_context: Any,
        event_queue: Any,
    ) -> None:
        remote_task_id = event_data.get("remote_task_id", "")

        await self._state_manager.save_input_required(
            InputRequiredState(
                task_id=task_id,
                call_context=call_context,
                remote_task_id=remote_task_id,
                workflow_id=event_data.get("workflow_id"),
            )
        )

        if event_queue is not None:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=conv_id,
                    status=TaskStatus(state=TASK_STATE_INPUT_REQUIRED),
                )
            )
        logger.info(
            f"[RequesterHandler] INPUT_REQUIRED: task={task_id}, "
            f"conv={conv_id}, remote_task_id={remote_task_id}"
        )

    async def _handle_failed(
        self,
        event_data: Dict[str, Any],
        task_id: str,
        conv_id: str,
        call_context: Any,
    ) -> None:
        error = event_data.get("error", "Agent failed")
        await self._state_manager.finalize_failed(task_id, call_context, error_text=error)
        logger.warning(
            f"[RequesterHandler] FAILED: task={task_id}, conv={conv_id}, "
            f"error={error!r:.80}"
        )
