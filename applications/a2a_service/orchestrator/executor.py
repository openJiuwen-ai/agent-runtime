"""
Executor — 核心编排逻辑（a2a-sdk 1.0.0-alpha.1，全量 v1.0 protobuf）。

职责：
  1. 实现 AgentExecutor 接口，由 user_router 或 DefaultRequestHandler 调用
  2. 首轮：调用 agent_stream_func()，处理 DelegateRequest / AnswerEvent
  3. DelegateRequest：调用 VersatileAdapter（A2A Client），根据返回决定续轮或挂起
  4. 续轮：从 context.current_task 读取 Task 状态，通过 Task.metadata 传递 va_task_id

Task 状态流转（存于 RedisTaskStore）：
  WORKING → [DelegateRequest + VA 无 end node] → INPUT_REQUIRED（metadata.va_task_id 已写入）
  INPUT_REQUIRED → [下一轮用户输入 + VA 有 end node] → WORKING → cascade → COMPLETED
  
Modified for dependency inversion: agent_stream_func is injected as parameter.
"""
from __future__ import annotations

import uuid
from typing import AsyncGenerator, Callable, Optional

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

from common.constants import session_request_key
from common.events import AgentEvent, DelegateRequest
from common.redis_client import RedisClient
from config import get_settings
from orchestrator.agent_adapter import agent_event_to_a2a
from common.redis_task_store import RedisTaskStore

_TTL = 1800


class Executor(AgentExecutor):
    def __init__(
        self,
        va_client: Client,
        redis: RedisClient,
        task_store: RedisTaskStore,
        agent_stream_func: Callable[..., AsyncGenerator[AgentEvent, None]] | None = None,
    ) -> None:
        self._va_client = va_client
        self._redis = redis
        self._task_store = task_store
        self._agent_stream_func = agent_stream_func

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
    ) -> None:
        if self._agent_stream_func is None:
            raise RuntimeError("agent_stream_func not injected - use create_app() or pass to Executor")
        
        async for event in self._agent_stream_func(
            query=query,
            conv_id=conv_id,
            cascade_result=cascade_result,
            context={"body": original_body},
        ):
            if isinstance(event, DelegateRequest):
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

    # ── VersatileAdapter 调用 ─────────────────────────────────────────────────

    def _build_va_message(
        self,
        query: str,
        headers: dict,
        body: dict,
        task_id: str = "",
        conv_id: str = "",
    ) -> SendMessageRequest:
        text_part = Part()
        text_part.text = query

        data_struct = Struct()
        data_struct.update({"headers": headers, "body": body})
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
                if isinstance(data, dict) and data.get("node_type") == "End":
                    return data
        return None

    def _extract_qa_node(self, event: TaskArtifactUpdateEvent) -> Optional[str]:
        target_node = get_settings().va_workflow_result_node
        if not target_node:
            return None
        for part in event.artifact.parts:
            if part.WhichOneof("content") == "data":
                data = MessageToDict(part.data)
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
            task_id="",
            conv_id=conv_id,
        )

        has_end_node = False
        final_result: dict | None = None
        qa_result: Optional[str] = None
        stream_resp_count = 0

        try:
            async for stream_resp in self._va_client.send_message(request):
                stream_resp_count += 1
                event = self._parse_stream_event(stream_resp)
                if event is None:
                    continue

                if va_real_task_id is None and hasattr(event, "task_id") and event.task_id:
                    va_real_task_id = event.task_id
                    logger.debug(
                        f"[Executor] VA real task_id={va_real_task_id}, conv={conv_id}"
                    )

                if isinstance(event, TaskArtifactUpdateEvent):
                    await event_queue.enqueue_event(event)

                    qa = self._extract_qa_node(event)
                    if qa is not None:
                        qa_result = qa

                    result = self._extract_end_node(event)
                    if result is not None:
                        has_end_node = True
                        final_result = result

        except Exception as e:
            logger.exception(f"[Executor] VA send_message 异常：{e}")

        logger.debug(f"[Executor] VA stream_resp_count={stream_resp_count}, conv={conv_id}")
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
        body = dict(original_body)
        body["stream"] = True

        request = self._build_va_message(
            query=user_input,
            headers=headers,
            body=body,
            task_id=va_task_id,
            conv_id=conv_id,
        )

        has_end_node = False
        final_result: dict | None = None
        qa_result: Optional[str] = None

        try:
            async for stream_resp in self._va_client.send_message(request):
                event = self._parse_stream_event(stream_resp)
                if event is None:
                    continue

                if isinstance(event, TaskArtifactUpdateEvent):
                    await event_queue.enqueue_event(event)

                    qa = self._extract_qa_node(event)
                    if qa is not None:
                        qa_result = qa

                    result = self._extract_end_node(event)
                    if result is not None:
                        has_end_node = True
                        final_result = result

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
                original_body=original_body,
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
