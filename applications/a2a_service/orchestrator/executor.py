"""
Executor — 核心编排逻辑（a2a-sdk 1.0.0-alpha.1，全量 v1.0 protobuf）。

职责：
  1. 实现 AgentExecutor 接口，由 user_router 或 DefaultRequestHandler 调用
  2. 首轮：调用 agent_stream()，处理 DelegateRequest / AnswerEvent
  3. DelegateRequest：调用 VersatileAdapter（A2A Client），根据返回决定续轮或挂起
  4. 续轮：从 context.current_task 读取 Task 状态，通过 Task.metadata 传递 va_task_id

Task 状态流转（存于 RedisTaskStore）：
  WORKING → [DelegateRequest + VA 无 end node] → INPUT_REQUIRED（metadata.va_task_id 已写入）
  INPUT_REQUIRED → [下一轮用户输入 + VA 有 end node] → WORKING → cascade → COMPLETED
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from a2a.client import Client
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    ROLE_USER,
    TASK_STATE_COMPLETED,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_WORKING,
)
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct, Value
from loguru import logger

from agents.EDPAgent import agent_stream
from common.constants import session_request_key
from common.events import (
    DelegateRequest,
    PlanningExecutionProcessEvent,
    ToolStartEvent,
)
from common.redis_client import RedisClient
from config import get_settings
from orchestrator.agent_adapter import agent_event_to_a2a
from common.redis_task_store import RedisTaskStore


def unwrap_versatile_response(data: dict) -> dict:
    """解包 Versatile 返回数据，返回 custom_rsp_data（原始 data 字段），异常安全"""
    try:
        if isinstance(data, dict):
            # 第一层解包：获取 custom_rsp_data
            custom_rsp = data.get("custom_rsp_data")
            if isinstance(custom_rsp, dict):
                # 第二层：获取原始 data 字段
                return custom_rsp.get("data", custom_rsp)
        return data
    except Exception as e:
        logger.warning(f"unwrap_versatile_response exception: {e}")
        return data  # 异常时返回原始数据


_TTL = 1800


class Executor(AgentExecutor):
    def __init__(
        self, va_client: Client, redis: RedisClient, task_store: RedisTaskStore
    ) -> None:
        self._va_client = va_client
        self._redis = redis
        self._task_store = task_store

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        conv_id = context.context_id or ""
        task_id = context.task_id or str(uuid.uuid4())
        call_context = context.call_context
        current_task = context.current_task

        # 从 message parts 提取 text、body
        user_query = ""
        original_headers: dict = {}
        original_body: dict = {}
        if context.message:
            for part in context.message.parts:
                which = part.WhichOneof("content")
                if which == "text" and not user_query:
                    user_query = part.text
                elif which == "data":
                    data = MessageToDict(part.data)
                    if isinstance(data, dict):
                        original_headers = data.get("headers", {})
                        original_body = data.get("body", data)

        # ── 续轮路径：Task 处于 INPUT_REQUIRED（VA 上次未完成）───────────────
        if current_task and current_task.status.state == TASK_STATE_INPUT_REQUIRED:
            meta = MessageToDict(current_task.metadata)
            va_task_id = meta.get("va_task_id", "")
            logger.info(
                f"[Executor] INPUT_REQUIRED 续轮：conv={conv_id}, va_task={va_task_id}"
            )
            await self._continue_versatile_adapter(
                conv_id=conv_id,
                task_id=task_id,
                call_context=call_context,
                va_task_id=va_task_id,
                user_input=user_query,
                headers=original_headers,
                original_body=original_body,
                event_queue=event_queue,
            )
            return

        # ── 首轮路径：DefaultRequestHandler 未创建 Task 时由 Executor 创建 ──
        if current_task is None:
            new_task = Task(
                id=task_id,
                context_id=conv_id,
                status=TaskStatus(state=TASK_STATE_WORKING),
            )
            await self._task_store.save(new_task, call_context)
            logger.debug(f"[Executor] 创建 Task：task={task_id}, conv={conv_id}")

        await self._run_agent(
            conv_id=conv_id,
            task_id=task_id,
            call_context=call_context,
            query=user_query,
            original_body=original_body,
            event_queue=event_queue,
            cascade_result=None,
            step_counter=[0],  # cascade 递归共享同一计数器
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass

    # ── 核心递归编排 ──────────────────────────────────────────────────────────

    async def _run_agent(
        self,
        conv_id: str,
        task_id: str,
        call_context: ServerCallContext,
        query: str,
        original_body: dict,
        event_queue: EventQueue,
        cascade_result: Optional[dict],
        step_counter: Optional[list[int]] = None,
    ) -> None:
        if step_counter is None:
            step_counter = [0]

        turn_start = time.monotonic()
        is_cascade = cascade_result is not None
        logger.info(
            f"[Executor] _run_agent 开始: conv={conv_id}, task={task_id}, "
            f"is_cascade={is_cascade}, step_counter={step_counter[0]}"
        )

        event_count = 0
        async for event in agent_stream(
            query=query,
            conv_id=conv_id,
            cascade_result=cascade_result,
            context={"body": original_body},
        ):
            event_count += 1
            logger.debug(
                f"[Executor] received agent event #{event_count}: "
                f"type={type(event).__name__}"
            )
            # ── step 边界发射 planning_execution_process ──────────────
            # 规则：ToolStartEvent 与 DelegateRequest 都计为一个"步骤"
            if isinstance(event, ToolStartEvent):
                step_counter[0] += 1
                desc = event.content or event.plugin or ""
                planning_content = (
                    f"[执行轨迹] 正在执行步骤{step_counter[0]}: {desc} "
                    f"(tool={event.plugin})"
                )
                planning = PlanningExecutionProcessEvent(content=planning_content)
                planning_a2a = agent_event_to_a2a(planning, task_id, conv_id)
                if planning_a2a is not None:
                    await event_queue.enqueue_event(planning_a2a)
                logger.info(
                    f"[Executor] step 边界: 步骤{step_counter[0]} "
                    f"(tool={event.plugin}, desc={desc!r:.60})"
                )

            if isinstance(event, DelegateRequest):
                step_counter[0] += 1
                planning_content = (
                    f"[执行轨迹] 正在执行步骤{step_counter[0]}: "
                    f"{event.task_description} "
                    f"(tool=adapter:versatile_proxy)"
                )
                planning = PlanningExecutionProcessEvent(content=planning_content)
                planning_a2a = agent_event_to_a2a(planning, task_id, conv_id)
                if planning_a2a is not None:
                    await event_queue.enqueue_event(planning_a2a)

                logger.info(
                    f"[Executor] step 边界: 步骤{step_counter[0]} "
                    f"(tool=adapter:versatile_proxy, intent={event.intent})"
                )
                logger.info(
                    f"[Executor] DelegateRequest → {event.intent}: "
                    f"{event.task_description!r:.60}"
                )
                va_result, va_task_id = await self._call_versatile_adapter(
                    delegate=event,
                    conv_id=conv_id,
                    task_id=task_id,
                    event_queue=event_queue,
                )
                if va_result is not None:
                    await self._run_agent(
                        conv_id=conv_id,
                        task_id=task_id,
                        call_context=call_context,
                        query=query,
                        original_body=original_body,
                        event_queue=event_queue,
                        cascade_result=va_result,
                        step_counter=step_counter,
                    )
                else:
                    # VA 未完成：将 va_task_id 写入 Task metadata，状态改为 INPUT_REQUIRED
                    task = await self._task_store.get(task_id, call_context)
                    if task:
                        task.metadata.update({"va_task_id": va_task_id or ""})
                        task.status.CopyFrom(
                            TaskStatus(state=TASK_STATE_INPUT_REQUIRED)
                        )
                        await self._task_store.save(task, call_context)
                    await event_queue.enqueue_event(
                        TaskStatusUpdateEvent(
                            task_id=task_id,
                            context_id=conv_id,
                            status=TaskStatus(state=TASK_STATE_INPUT_REQUIRED),
                        )
                    )
                    logger.info(
                        f"[Executor] VA 挂起：conv={conv_id}, va_task={va_task_id}"
                    )
                # 记录 VA 挂起 / cascade 路径结束时的累计耗时
                turn_duration_ms = (time.monotonic() - turn_start) * 1000
                logger.info(
                    f"[Executor] ⏱️ _run_agent 返回: conv={conv_id}, "
                    f"duration={turn_duration_ms:.2f}ms, "
                    f"events_received={event_count}, steps={step_counter[0]}"
                )
                return

            a2a_event = agent_event_to_a2a(event, task_id, conv_id)
            if a2a_event:
                await event_queue.enqueue_event(a2a_event)

        # agent stream 正常结束（非 DelegateRequest 路径）→ 写 COMPLETED 到 TaskStore
        task = await self._task_store.get(task_id, call_context)
        if task and task.status.state != TASK_STATE_COMPLETED:
            task.status.CopyFrom(TaskStatus(state=TASK_STATE_COMPLETED))
            await self._task_store.save(task, call_context)
            logger.debug(f"[Executor] Task 标记 COMPLETED：task={task_id}, conv={conv_id}")

        # 本轮（或本次 cascade）正常结束，打总耗时
        turn_duration_ms = (time.monotonic() - turn_start) * 1000
        logger.info(
            f"[Executor] ⏱️ _run_agent 正常结束: conv={conv_id}, "
            f"duration={turn_duration_ms:.2f}ms, "
            f"events_received={event_count}, steps_accumulated={step_counter[0]}"
        )

    # ── VersatileAdapter 调用 ─────────────────────────────────────────────────

    def _build_va_message(
        self,
        query: str,
        headers: dict,
        body: dict,
        task_id: str = "",
        conv_id: str = "",
        params: Optional[dict] = None,
    ) -> SendMessageRequest:
        text_part = Part()
        text_part.text = query

        data_struct = Struct()
        data_struct.update({"headers": headers, "body": body, "params": params or {}})
        data_value = Value()
        data_value.struct_value.CopyFrom(data_struct)
        data_part = Part()
        data_part.data.CopyFrom(data_value)

        msg = Message(
            role=ROLE_USER,
            message_id=str(uuid.uuid4()),
            task_id=task_id,
            context_id=conv_id,
        )
        msg.parts.extend([text_part, data_part])
        return SendMessageRequest(message=msg)

    def _parse_stream_event(self, stream_resp):
        which = (
            stream_resp.WhichOneof("payload")
            if hasattr(stream_resp, "WhichOneof")
            else None
        )
        if which == "artifact_update":
            return stream_resp.artifact_update
        if which == "status_update":
            return stream_resp.status_update
        return None

    def _extract_end_node(self, event: TaskArtifactUpdateEvent) -> Optional[dict]:
        for part in event.artifact.parts:
            if part.WhichOneof("content") == "data":
                data = MessageToDict(part.data)
                data = unwrap_versatile_response(data)  # 先解包！
                if isinstance(data, dict) and data.get("node_type") == "End":
                    return data
        return None

    def _is_suppressed_node(self, event: TaskArtifactUpdateEvent) -> bool:
        """判断该 artifact 是否为配置中需要屏蔽的节点（不推送给用户）。"""
        target = get_settings().va_workflow_result_node
        if not target:
            return False
        for part in event.artifact.parts:
            if part.WhichOneof("content") == "data":
                data = MessageToDict(part.data)
                data = unwrap_versatile_response(data)  # 先解包！
                if isinstance(data, dict) and data.get("node_name") == target:
                    return True
        return False

    def _extract_qa_node(self, event: TaskArtifactUpdateEvent) -> Optional[str]:
        target_node = get_settings().va_workflow_result_node
        if not target_node:
            return None
        for part in event.artifact.parts:
            if part.WhichOneof("content") == "data":
                data = MessageToDict(part.data)
                data = unwrap_versatile_response(data)  # 先解包！
                if (
                    isinstance(data, dict)
                    and data.get("node_type") == "QA"
                    and data.get("node_name") == target_node
                ):
                    return data.get("text", "") or None
        return None

    async def _call_versatile_adapter(
        self,
        delegate: DelegateRequest,
        conv_id: str,
        task_id: str,
        event_queue: EventQueue,
    ) -> tuple[Optional[dict], Optional[str]]:
        """DPA 委托场景：从 Redis 取首轮缓存，替换 query/intent 后发给 VA。"""
        cached = await self._redis.get_json(session_request_key(conv_id)) or {}
        headers = cached.get("headers", {})
        body = dict(cached.get("body", {}))
        params = cached.get("params", {})
        # 同时修改 custom_data.inputs 和 input（兼容两种格式）
        if "custom_data" in body and isinstance(body["custom_data"], dict):
            # 创建 custom_data 的副本，避免修改原始数据
            custom_data = dict(body["custom_data"])
            if "inputs" in custom_data and isinstance(custom_data["inputs"], dict):
                # 创建 inputs 的副本
                inputs = dict(custom_data["inputs"])
                inputs["query"] = delegate.task_description
                inputs["intent"] = delegate.intent
                custom_data["inputs"] = inputs
            # 更新 body 中的 custom_data
            body["custom_data"] = custom_data
        input_section = dict(body.get("input") or {})
        input_section["query"] = delegate.task_description
        input_section["intent"] = delegate.intent
        body["input"] = input_section
        body["stream"] = True

        va_real_task_id: Optional[str] = None

        request = self._build_va_message(
            query=delegate.task_description,
            headers=headers,
            body=body,
            params=params,
            task_id="",
            conv_id=conv_id,
        )

        has_end_node = False
        qa_result: Optional[str] = None
        stream_resp_count = 0
        forwarded_count = 0
        suppressed_count = 0
        va_call_start = time.monotonic()

        logger.info(
            f"[Executor] [VersatileProxy] 开始调用 VA: conv={conv_id}, "
            f"intent={delegate.intent}, task_desc={delegate.task_description!r:.60}"
        )

        try:
            async for stream_resp in self._va_client.send_message(request):
                stream_resp_count += 1
                event = self._parse_stream_event(stream_resp)
                if event is None:
                    logger.debug(
                        f"[Executor] [VersatileProxy] chunk #{stream_resp_count} "
                        f"解析为 None，跳过"
                    )
                    continue

                if va_real_task_id is None and hasattr(event, "task_id") and event.task_id:
                    va_real_task_id = event.task_id
                    logger.debug(
                        f"[Executor] VA real task_id={va_real_task_id}, conv={conv_id}"
                    )

                if isinstance(event, TaskArtifactUpdateEvent):
                    if self._is_suppressed_node(event):
                        suppressed_count += 1
                        logger.debug(
                            f"[Executor] [VersatileProxy] chunk #{stream_resp_count} "
                            f"命中 va_workflow_result_node，抑制不推送"
                        )
                    else:
                        await event_queue.enqueue_event(event)
                        forwarded_count += 1
                        logger.debug(
                            f"[Executor] [VersatileProxy] chunk #{stream_resp_count} "
                            f"已转发到 event_queue"
                        )

                    qa = self._extract_qa_node(event)
                    if qa is not None:
                        qa_result = qa
                        logger.debug(
                            f"[Executor] [VersatileProxy] 提取到 QA 节点 text: "
                            f"{qa!r:.60}"
                        )

                    if self._extract_end_node(event) is not None:
                        has_end_node = True
                        logger.debug(
                            f"[Executor] [VersatileProxy] 检测到 End node，"
                            f"将进入 cascade 路径"
                        )

        except Exception as e:
            logger.exception(f"[Executor] [VersatileProxy] VA send_message 异常：{e}")

        va_duration_ms = (time.monotonic() - va_call_start) * 1000
        logger.info(
            f"[Executor] [VersatileProxy] ⏱️ VA 调用结束: duration={va_duration_ms:.2f}ms, "
            f"chunks={stream_resp_count}, forwarded={forwarded_count}, "
            f"suppressed={suppressed_count}, has_end_node={has_end_node}"
        )
        continuation_task_id = va_real_task_id or str(uuid.uuid4())

        if has_end_node:
            cascade = {"workflow_result": qa_result}
            logger.info(
                f"[Executor] VA end node: conv={conv_id}, qa_result={qa_result!r:.60}"
            )
            return cascade, continuation_task_id

        logger.info(
            f"[Executor] VA 无 end node: conv={conv_id}, va_task={continuation_task_id}"
        )
        return None, continuation_task_id

    async def _continue_versatile_adapter(
        self,
        conv_id: str,
        task_id: str,
        call_context: ServerCallContext,
        va_task_id: str,
        user_input: str,
        headers: dict,
        original_body: dict,
        event_queue: EventQueue,
    ) -> None:
        """VA 挂起后，下一轮用户输入续轮。va_task_id 为 VA 真实 task_id。"""

        # 从 Redis 读取缓存的首轮请求（优先使用）
        cached = await self._redis.get_json(session_request_key(conv_id)) or {}
        first_body = cached.get("body", original_body)
        params = cached.get("params", {})
        body = dict(original_body)
        body["stream"] = True

        request = self._build_va_message(
            query=user_input,
            headers=headers,
            body=body,
            params=params,
            task_id=va_task_id,
            conv_id=conv_id,
        )

        has_end_node = False
        qa_result: Optional[str] = None

        try:
            async for stream_resp in self._va_client.send_message(request):
                event = self._parse_stream_event(stream_resp)
                if event is None:
                    continue

                if isinstance(event, TaskArtifactUpdateEvent):
                    if not self._is_suppressed_node(event):
                        await event_queue.enqueue_event(event)

                    qa = self._extract_qa_node(event)
                    if qa is not None:
                        qa_result = qa

                    if self._extract_end_node(event) is not None:
                        has_end_node = True

        except Exception as e:
            logger.exception(f"[Executor] VA continue send_message 异常：{e}")

        if has_end_node:
            cascade = {"workflow_result": qa_result}
            logger.info(
                f"[Executor] VA 续轮 end node: conv={conv_id}, qa_result={qa_result!r:.60}"
            )
            # 恢复 Task 到 WORKING 状态后做 cascade 续轮
            task = await self._task_store.get(task_id, call_context)
            if task:
                task.status.CopyFrom(TaskStatus(state=TASK_STATE_WORKING))
                await self._task_store.save(task, call_context)

            await self._run_agent(
                conv_id=conv_id,
                task_id=task_id,
                call_context=call_context,
                query="",
                original_body=first_body,  # 使用首轮请求的 body
                event_queue=event_queue,
                cascade_result=cascade,
            )
        else:
            # VA 仍未完成，继续挂起；va_task_id 不变
            task = await self._task_store.get(task_id, call_context)
            if task:
                task.metadata.update({"va_task_id": va_task_id or ""})
                task.status.CopyFrom(TaskStatus(state=TASK_STATE_INPUT_REQUIRED))
                await self._task_store.save(task, call_context)
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=conv_id,
                    status=TaskStatus(state=TASK_STATE_INPUT_REQUIRED),
                )
            )
            logger.info(
                f"[Executor] VA 续轮仍无 end node: conv={conv_id}, va_task={va_task_id}"
            )
