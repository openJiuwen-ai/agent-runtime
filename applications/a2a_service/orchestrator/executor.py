# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

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

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from a2a.client import Client
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Message,
    Artifact,
    Part,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    ROLE_AGENT,
    ROLE_USER,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_WORKING,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct, Value
from loguru import logger

from agents.EDPAgent import agent_stream
from common.constants import session_request_key
from common.events import (
    DelegateRequest,
    PlanningExecutionProcessEvent,
    ToolStartEvent,
)
from common.logger import (
    Extra,
    Tag,
    build_versatile_end_observation,
    build_versatile_start_observation,
    mask_sensitive_fields,
    to_logger,
    Level
)
from common.redis_client import RedisClient
from config import get_settings
from orchestrator.agent_adapter import agent_event_to_a2a
from common.redis_task_store import RedisTaskStore

_TTL = 1800


def _safe_dump_event(event: Any) -> str:
    """把 a2a 事件（protobuf）序列化为 JSON 字符串，应用敏感字段脱敏。

    序列化失败时返回错误占位符，保证调用方拿到的永远是 ``str``，
    便于落 DEBUG 日志而不影响主链路。
    """
    try:
        return json.dumps(
            mask_sensitive_fields(MessageToDict(event)),
            ensure_ascii=False,
        )
    except Exception as dump_exc:
        return f"<dump failed: {dump_exc}>"


def _log_va_chunk_debug(stream_resp_count: int, event: Any) -> None:
    """DEBUG 级埋点：打印 a2a_service 从 VersatileAdapter 接收到的每一帧 chunk 内容。

    使用 ``logger.opt(lazy=True)`` 延迟求值：DEBUG 未启用时不会触发 MessageToDict
    与 json.dumps，避免在生产 INFO 级别下白白消耗 CPU。
    """
    with logger.contextualize(tag=Tag.TAG_VERSATILE_CHUNK, cost=0):
        logger.opt(lazy=True).debug(
            "[Executor] [VersatileProxy] chunk #{} payload={}",
            lambda c=stream_resp_count: c,
            lambda e=event: _safe_dump_event(e),
        )


def _rewrite_recommend_delegate(intent: str, task_description: str) -> tuple[str, str]:
    """临时兼容旧链路：推荐首跳改写为平台历史上可识别的入口。"""
    if intent != "理财推荐":
        return intent, task_description

    normalized_query = (task_description or "").strip()
    if not normalized_query or normalized_query == "推荐理财产品":
        normalized_query = "请推荐低风险理财产品"

    return "理财选品购买", normalized_query


@dataclass(frozen=True)
class _TurnContext:
    """单轮 Executor 编排所需的上下文。

    将相关性较强的会话/任务/调用句柄打包传递，避免在内部方法间传入
    个数较多的散参数（参考 G.FNM.03）。
    """

    conv_id: str
    task_id: str
    call_context: ServerCallContext
    event_queue: EventQueue


@dataclass(frozen=True)
class _VaRequestPayload:
    """构造 VersatileAdapter ``SendMessageRequest`` 的载荷集合。

    ``headers`` / ``body`` / ``params`` 在调用层共同决定下游工作流入参，
    通过统一的 dataclass 进行命名封装（参考 G.FNM.03）。
    """

    query: str
    headers: dict
    body: dict
    params: Optional[dict] = None
    task_id: str = ""
    conv_id: str = ""
    trace_id: str = ""
    agent_id: str = ""
    target: Optional[dict] = None


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

        turn_ctx = _TurnContext(
            conv_id=conv_id,
            task_id=task_id,
            call_context=call_context,
            event_queue=event_queue,
        )

        # ── 续轮路径：Task 处于 INPUT_REQUIRED（VA 上次未完成）───────────────
        if current_task and current_task.status.state == TASK_STATE_INPUT_REQUIRED:
            meta = MessageToDict(current_task.metadata)
            va_task_id = meta.get("va_task_id", "")
            logger.info(
                f"[Executor] INPUT_REQUIRED 续轮：conv={conv_id}, va_task={va_task_id}"
            )
            await self._continue_versatile_adapter(
                turn_ctx,
                va_task_id=va_task_id,
                user_input=user_query,
                headers=original_headers,
                original_body=original_body,
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

        await self.run_agent(
            turn_ctx,
            query=user_query,
            original_body=original_body,
            cascade_result=None,
            step_counter=[0],  # cascade 递归共享同一计数器
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass

    # ── 核心递归编排 ──────────────────────────────────────────────────────────

    async def run_agent(
        self,
        turn_ctx: _TurnContext,
        query: str,
        original_body: dict,
        cascade_result: Optional[dict],
        step_counter: Optional[list[int]] = None,
    ) -> None:
        if step_counter is None:
            step_counter = [0]

        conv_id = turn_ctx.conv_id
        task_id = turn_ctx.task_id
        call_context = turn_ctx.call_context
        event_queue = turn_ctx.event_queue

        turn_start = time.monotonic()
        is_cascade = cascade_result is not None
        logger.info(
            f"[Executor] run_agent 开始: conv={conv_id}, task={task_id}, "
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
                va_result, va_task_id, finalized = await self._call_versatile_adapter(
                    turn_ctx,
                    delegate=event,
                )
                if va_result is not None:
                    await self.run_agent(
                        turn_ctx,
                        query=query,
                        original_body=original_body,
                        cascade_result=va_result,
                        step_counter=step_counter,
                    )
                elif finalized:
                    # VA 上游报错路径：_call_versatile_adapter 已写 FAILED + 推 FAILED 事件
                    pass
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
                    f"[Executor] ⏱️ run_agent 返回: conv={conv_id}, "
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
            f"[Executor] ⏱️ run_agent 正常结束: conv={conv_id}, "
            f"duration={turn_duration_ms:.2f}ms, "
            f"events_received={event_count}, steps_accumulated={step_counter[0]}"
        )

    # ── VersatileAdapter 调用 ─────────────────────────────────────────────────

    def _build_va_message(self, payload: _VaRequestPayload) -> SendMessageRequest:
        text_part = Part()
        text_part.text = payload.query

        data_struct = Struct()
        data_struct.update(
            {
                "headers": payload.headers,
                "body": payload.body,
                "params": payload.params or {},
                "trace_id": payload.trace_id,
                "agent_id": payload.agent_id,
                "target": payload.target or {},
            }
        )
        data_value = Value()
        data_value.struct_value.CopyFrom(data_struct)
        data_part = Part()
        data_part.data.CopyFrom(data_value)

        msg = Message(
            role=ROLE_USER,
            message_id=str(uuid.uuid4()),
            task_id=payload.task_id,
            context_id=payload.conv_id,
        )
        msg.parts.extend([text_part, data_part])
        return SendMessageRequest(message=msg)

    def _parse_stream_event(self, stream_resp):
        if stream_resp.HasField("artifact_update"):
            return stream_resp.artifact_update
        if stream_resp.HasField("status_update"):
            return stream_resp.status_update
        return None

    @staticmethod
    def _format_upstream_error(err: dict) -> str:
        """把上游 error/exception 的 data 拼成可展示的错误描述字符串。"""
        code = err.get("code")
        message = err.get("message") or err.get("msg") or ""
        if code:
            return f"执行报错，错误码：{code}，错误信息：{message}"
        return message or "VA 上游报错"

    def _build_failed_status_event(
        self, task_id: str, conv_id: str, error_text: str
    ) -> TaskStatusUpdateEvent:
        """构造带错误描述的 ``TaskStatusUpdateEvent(FAILED)``。

        ``status.message.parts[0].text`` 由 user_router._extract_event_meta 转为
        前端可见的 ``custom_rsp_data.event=interrupt_start`` 帧的 content/error 字段。
        """
        msg = Message(
            role=ROLE_AGENT,
            message_id=str(uuid.uuid4()),
            task_id=task_id,
            context_id=conv_id,
            parts=[Part(text=error_text)],
        )
        return TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=conv_id,
            status=TaskStatus(state=TASK_STATE_FAILED, message=msg),
        )

    async def _finalize_failed(
        self,
        turn_ctx: _TurnContext,
        upstream_error: dict,
    ) -> None:
        """VA 上游报错统一收尾：task FAILED + 清 va_task_id + enqueue FAILED 事件。

        让下次同 conv_id 请求重新走首轮（不再走续轮路径用 stale va_task_id 调 VA），
        破解原 INPUT_REQUIRED 路径下的 conversation 锁死。
        """
        task_id = turn_ctx.task_id
        conv_id = turn_ctx.conv_id
        error_text = self._format_upstream_error(upstream_error)

        task = await self._task_store.get(task_id, turn_ctx.call_context)
        if task:
            task.metadata.update({"va_task_id": ""})
            task.status.CopyFrom(TaskStatus(state=TASK_STATE_FAILED))
            await self._task_store.save(task, turn_ctx.call_context)

        await turn_ctx.event_queue.enqueue_event(
            self._build_failed_status_event(task_id, conv_id, error_text),
        )
        logger.warning(
            f"[Executor] VA 上游报错，task FAILED：conv={conv_id}, "
            f"code={upstream_error.get('code')}, "
            f"msg={(upstream_error.get('message') or '')!r:.80}"
        )

    def _extract_workflow_result(self, event: TaskStatusUpdateEvent) -> Optional[str]:
        """从 VA 的 COMPLETED 状态事件 message 中提取 workflow_result。

        VA 侧在 updater.complete(message) 时通过 Part 的 metadata {"vatype": "workflow_result"} 标识，
        DataPart 为纯文本 string_value。
        """
        if not event.status or not event.status.message:
            return None
        for part in event.status.message.parts:
            if not part.HasField("text") or not part.HasField("metadata"):
                continue
            meta = MessageToDict(part.metadata)
            if meta.get("vatype") == "workflow_result":
                return part.text
        return None

    def _extract_failed_error(self, event: TaskStatusUpdateEvent) -> Optional[dict]:
        """从 VA 的 FAILED 状态事件 message 中提取上游错误详情。

        VA 侧在 updater.failed(message) 时通过 Part 的 metadata {"vatype": "upstream_error"} 标识，
        text 为上游 exception 帧原始 JSON。解析失败时返回 {"message": <raw_text>}。
        无 message 时返回 None（调用方使用兜底通用文案）。
        """
        if not event.status or not event.status.message:
            return None
        for part in event.status.message.parts:
            if not part.HasField("text") or not part.HasField("metadata"):
                continue
            meta = MessageToDict(part.metadata)
            if meta.get("vatype") != "upstream_error":
                continue
            raw_text = part.text or ""
            if not raw_text:
                continue
            try:
                parsed = json.loads(raw_text)
            except (ValueError, TypeError):
                return {"message": raw_text}
            if isinstance(parsed, dict):
                # 兼容 {"event": "exception", "data": {code, message}} 与扁平 {code, message}
                inner = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
                return inner
            return {"message": raw_text}
        return None

    async def _forward_artifact(
        self, turn_ctx: _TurnContext, event: TaskArtifactUpdateEvent, event_queue: EventQueue,
    ) -> None:
        """转发 VA artifact 事件：将 text Part 转换为 data Part 后入队。"""
        parts = []
        for part in event.artifact.parts:
            if not part.HasField("text") or not part.HasField("metadata"):
                continue
            meta = MessageToDict(part.metadata)
            if meta.get("vatype") != "data_proxy":
                continue
            try:
                parsed = json.loads(part.text)
            except ValueError:
                logger.error("[Executor] [VersatileProxy] 待转发内容不是结构化信息，跳过不处理")
                continue
            new_part = Part(data=ParseDict(parsed, Value()), media_type=part.media_type)
            parts.append(new_part)

        # parts 为空时不入队，避免下游产出空 thought SSE 帧（次要问题 #2）
        if not parts:
            return

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=turn_ctx.task_id,
                context_id=turn_ctx.conv_id,
                artifact=Artifact(
                    artifact_id=event.artifact.artifact_id,
                    name=event.artifact.name,
                    parts=parts,
                    metadata=event.artifact.metadata,
                    extensions=event.artifact.extensions,
                ),
                append=event.append,
                last_chunk=event.last_chunk,
            )
        )

    async def _call_versatile_adapter(
        self,
        turn_ctx: _TurnContext,
        delegate: DelegateRequest,
    ) -> tuple[Optional[dict], str, bool]:
        """DPA 委托场景：从 Redis 取首轮缓存，替换 query/intent 后发给 VA。

        Returns: ``(cascade, va_task_id, finalized)``。
            - ``cascade`` 非 None 时上层走 cascade 续轮；
            - ``finalized=True`` 表示 VA 上游报错路径已在内部把 Task 标 FAILED 并
              enqueue TaskStatusUpdateEvent(FAILED)，上层应直接 ``return``，
              不再走 INPUT_REQUIRED 分支（避免覆盖 FAILED 状态、避免锁死 conv_id）。
        """
        conv_id = turn_ctx.conv_id
        event_queue = turn_ctx.event_queue

        cached = await self._redis.get_json(session_request_key(conv_id)) or {}
        headers = cached.get("headers", {})
        body = dict(cached.get("body", {}))
        params = cached.get("params", {})
        trace_id = cached.get("trace_id", "")

        effective_intent, effective_query = _rewrite_recommend_delegate(
            delegate.intent,
            delegate.task_description,
        )
        if effective_intent != delegate.intent or effective_query != delegate.task_description:
            logger.info(
                "[Executor] 推荐入口临时改写：intent={} -> {}, query={!r} -> {!r}",
                delegate.intent,
                effective_intent,
                delegate.task_description,
                effective_query,
            )

        input_section = dict(body.get("input") or {})
        input_section["query"] = effective_query
        input_section["intent"] = effective_intent
        body["input"] = input_section

        custom_data = dict(body.get("custom_data") or {})
        custom_inputs = dict(custom_data.get("inputs") or {})
        custom_inputs["query"] = effective_query
        custom_inputs["intent"] = effective_intent
        custom_data["inputs"] = custom_inputs
        body["custom_data"] = custom_data

        body["stream"] = True

        # 在 a2a 调用侧记录 Versatile 前后 Tag 日志
        versatile_call_id = str(uuid.uuid4())
        versatile_name = get_settings().versatile_adapter_url or "versatile_adapter"
        call_started_ms = int(time.time() * 1000)
        status_message = 0
        error_message: Optional[str] = None

        va_real_task_id: Optional[str] = None
        continuation_task_id = ""

        # 从 delegate.target_agent 获取 agent_id
        agent_id = delegate.target_agent or ""

        request = self._build_va_message(
            _VaRequestPayload(
                query=effective_query,
                headers=headers,
                body=body,
                params=params,
                task_id="",
                conv_id=conv_id,
                trace_id=trace_id,
                agent_id=agent_id,
                target={"type": "workflow", "intent": effective_intent} if effective_intent else None,
            )
        )

        has_end_node = False
        qa_result: Optional[str] = None
        upstream_error: Optional[dict] = None
        stream_resp_count = 0
        forwarded_count = 0
        logger.info(
            f"[Executor] [VersatileProxy] 开始调用 VA: conv={conv_id}, "
            f"intent={delegate.intent}, task_desc={delegate.task_description!r:.60}"
        )

        # 调用前打点：记录请求头/体快照
        to_logger(
            message=build_versatile_start_observation(
                call_id=versatile_call_id,
                name=versatile_name,
                request_headers=headers,
                request_body=body,
            ),
            extra=Extra(tag=Tag.TAG_VERSATILE_START, cost=0),
        )

        # 收集所有 chunk 用于最终拼接
        all_chunks = []
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

                # DEBUG 级：打印从 VersatileAdapter 接收到的整帧报文（步骤 7 首轮路径）
                _log_va_chunk_debug(stream_resp_count, event)
                # 收集 chunk 信息
                chunk_info = {
                    "index": stream_resp_count,
                    "event_type": type(event).__name__,
                    "content": _safe_dump_event(event),
                }
                all_chunks.append(chunk_info)

                if va_real_task_id is None and hasattr(event, "task_id") and event.task_id:
                    va_real_task_id = event.task_id
                    logger.debug(
                        f"[Executor] VA real task_id={va_real_task_id}, conv={conv_id}"
                    )

                if isinstance(event, TaskArtifactUpdateEvent):
                    # data_proxy → 转换 text Part 后转发前端
                    await self._forward_artifact(turn_ctx, event, event_queue)
                    forwarded_count += 1

                elif isinstance(event, TaskStatusUpdateEvent):
                    if event.status.state == TASK_STATE_COMPLETED:
                        has_end_node = True
                        # 从 COMPLETED 状态事件的 message 中提取 workflow_result
                        wr = self._extract_workflow_result(event)
                        if wr is not None:
                            qa_result = wr
                            logger.debug(
                                f"[Executor] [VersatileProxy] chunk #{stream_resp_count} "
                                f"status COMPLETED, workflow_result={wr!r:.60}"
                            )
                        logger.debug("[Executor] VA TaskStatusUpdateEvent(COMPLETED)")
                    elif event.status.state == TASK_STATE_FAILED:
                        if upstream_error is None:
                            extracted = self._extract_failed_error(event)
                            upstream_error = extracted if extracted else {"message": "VA 任务异常终止"}
                        logger.debug(
                            f"[Executor] VA TaskStatusUpdateEvent(FAILED), upstream_error={upstream_error!r:.120}"
                        )

        except Exception as e:
            status_message = 1
            error_message = str(e)
            logger.exception(f"[Executor] VA send_message 异常：{e}")
        finally:
            # 无论成功/异常都补打结束日志，保证调用可观测性完整
            duration_ms = int(time.time() * 1000) - call_started_ms
            continuation_task_id = va_real_task_id or str(uuid.uuid4())
            output_payload = {
                "stream_resp_count": stream_resp_count,
                "has_end_node": has_end_node,
                "va_task_id": continuation_task_id,
                "all_chunks": all_chunks,
            }
            if error_message:
                output_payload["error"] = error_message
            to_logger(
                level="ERROR" if status_message else "INFO",
                message=build_versatile_end_observation(
                    call_id=versatile_call_id,
                    name=versatile_name,
                    output_payload=output_payload,
                    status_message=status_message,
                    duration_ms=duration_ms,
                ),
                extra=Extra(tag=Tag.TAG_VERSATILE_END, cost=max(duration_ms, 0)),
            )

        if has_end_node:
            cascade = (
                {"workflow_result": qa_result} if qa_result is not None else {}
            )
            logger.info(
                f"[Executor] VA end node: conv={conv_id}, qa_result={qa_result!r:.60}"
            )
            return cascade, continuation_task_id, False

        if upstream_error is not None:
            await self._finalize_failed(turn_ctx, upstream_error)
            return None, "", True

        logger.info(
            f"[Executor] VA 无 end node: conv={conv_id}, va_task={continuation_task_id}"
        )
        return None, continuation_task_id, False

    async def _continue_versatile_adapter(
        self,
        turn_ctx: _TurnContext,
        va_task_id: str,
        user_input: str,
        headers: dict,
        original_body: dict,
    ) -> None:
        """VA 挂起后，下一轮用户输入续轮。va_task_id 为 VA 真实 task_id。"""
        conv_id = turn_ctx.conv_id
        task_id = turn_ctx.task_id
        call_context = turn_ctx.call_context
        event_queue = turn_ctx.event_queue

        # params 仍从 Redis 首轮缓存取（保留 HEAD 的 params URL query 参数透传）
        cached = await self._redis.get_json(session_request_key(conv_id)) or {}
        params = cached.get("params", {})
        trace_id = cached.get("trace_id", "")
        agent_id = cached.get("agent_id", "")
        # 对齐 YGQ：续轮直接使用当前请求携带的 body，确保 buyStatus/tranNo 等
        # 当前轮输入能透传给下游工作流，而不是回退到首轮缓存 body。
        body = dict(original_body)
        body["stream"] = True

        # 在 a2a 续轮调用侧记录 Versatile 前后 Tag 日志
        versatile_call_id = str(uuid.uuid4())
        versatile_name = get_settings().versatile_adapter_url or "versatile_adapter"
        call_started_ms = int(time.time() * 1000)
        status_message = 0
        error_message: Optional[str] = None

        request = self._build_va_message(
            _VaRequestPayload(
                query=user_input,
                headers=headers,
                body=body,
                params=params,
                task_id=va_task_id,
                conv_id=conv_id,
                trace_id=trace_id,
                agent_id=agent_id,
            )
        )

        has_end_node = False
        qa_result: Optional[str] = None
        upstream_error: Optional[dict] = None
        stream_resp_count = 0

        # 续轮调用前打点，记录本次输入上下文
        to_logger(
            message=build_versatile_start_observation(
                call_id=versatile_call_id,
                name=versatile_name,
                request_headers=headers,
                request_body=body,
            ),
            extra=Extra(tag=Tag.TAG_VERSATILE_START, cost=0),
        )

        # 收集所有 chunk 用于最终拼接
        all_chunks = []
        try:
            async for stream_resp in self._va_client.send_message(request):
                stream_resp_count += 1
                event = self._parse_stream_event(stream_resp)
                if event is None:
                    continue

                # 收集 chunk 信息
                chunk_info = {
                    "index": stream_resp_count,
                    "event_type": type(event).__name__,
                    "content": _safe_dump_event(event),
                }
                all_chunks.append(chunk_info)

                # DEBUG 级：打印从 VersatileAdapter 接收到的整帧报文（步骤 7 续轮路径）
                _log_va_chunk_debug(stream_resp_count, event)

                if isinstance(event, TaskArtifactUpdateEvent):
                    # data_proxy → 转换 text Part 后转发前端
                    await self._forward_artifact(turn_ctx, event, event_queue)

                elif isinstance(event, TaskStatusUpdateEvent):
                    if event.status.state == TASK_STATE_COMPLETED:
                        has_end_node = True
                        # 从 COMPLETED 状态事件的 message 中提取 workflow_result
                        wr = self._extract_workflow_result(event)
                        if wr is not None:
                            qa_result = wr
                        logger.debug("[Executor] VA 续轮 TaskStatusUpdateEvent(COMPLETED)")
                    elif event.status.state == TASK_STATE_FAILED:
                        if upstream_error is None:
                            extracted = self._extract_failed_error(event)
                            upstream_error = extracted if extracted else {"message": "VA 任务异常终止"}
                        logger.debug(
                            f"[Executor] VA 续轮 TaskStatusUpdateEvent(FAILED), upstream_error={upstream_error!r:.120}"
                        )

        except Exception as e:
            status_message = 1
            error_message = str(e)
            logger.exception(f"[Executor] VA continue send_message 异常：{e}")
        finally:
            # 续轮结束统一打点，补充状态与耗时
            duration_ms = int(time.time() * 1000) - call_started_ms
            output_payload = {
                "stream_resp_count": stream_resp_count,
                "has_end_node": has_end_node,
                "va_task_id": va_task_id,
                "all_chunks": all_chunks,
            }
            if error_message:
                output_payload["error"] = error_message
            to_logger(
                level="ERROR" if status_message else "INFO",
                message=build_versatile_end_observation(
                    call_id=versatile_call_id,
                    name=versatile_name,
                    output_payload=output_payload,
                    status_message=status_message,
                    duration_ms=duration_ms,
                ),
                extra=Extra(tag=Tag.TAG_VERSATILE_END, cost=max(duration_ms, 0)),
            )

        if has_end_node:
            cascade = (
                {"workflow_result": qa_result} if qa_result is not None else {}
            )
            logger.info(
                f"[Executor] VA 续轮 end node: conv={conv_id}, qa_result={qa_result!r:.60}"
            )
            # 恢复 Task 到 WORKING 状态后做 cascade 续轮
            task = await self._task_store.get(task_id, call_context)
            if task:
                task.status.CopyFrom(TaskStatus(state=TASK_STATE_WORKING))
                await self._task_store.save(task, call_context)

            await self.run_agent(
                turn_ctx,
                query="",
                original_body=original_body,
                cascade_result=cascade,
            )
        elif upstream_error is not None:
            # VA 续轮也报错：同样落 FAILED + 清空 va_task_id，破解 conv_id 锁死
            await self._finalize_failed(turn_ctx, upstream_error)
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
