# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Executor — 核心编排入口（薄封装，依赖 Route + State）。

职责：
  1. 实现 AgentExecutor 接口，由 api.dispatch 或 DefaultRequestHandler 调用
  2. execute()：解析请求 → 构建 NormalizedEvent → dispatch → return
  3. run_agent()：迭代 agent_stream，归一化事件 → dispatch
  4. 不包含任何 if target.type 分支判断，所有业务逻辑由 handler 处理

Task 状态流转（存于 RedisTaskStore）：
  WORKING → [DelegateRequest + VA 无 end node] → INPUT_REQUIRED（metadata.remote_task_id + source_agent 已写入）
  INPUT_REQUIRED → [下一轮用户输入 + VA 有 end node] → WORKING → cascade → COMPLETED
"""
from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    TaskStatus,
    TaskStatusUpdateEvent,
    ROLE_AGENT,
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
)
from google.protobuf.json_format import MessageToDict
from loguru import logger

from channels.dict_to_a2a import clear_artifact_id_cache
from channels.observability import log_channel_event
from common.constants import session_request_key
from common.logger import Level
from common.redis_client import RedisClient
from config import get_settings
from orchestrator.heartbeat_runtime import HeartbeatRuntimeManager
from orchestrator.route import (
    NormalizedEvent,
    RouteDispatcher,
)
from orchestrator.state import TaskStateManager, CONV_TASK_KEY

_TTL = 1800

# 兼容测试 monkeypatch：优先使用模块级变量；未注入时再懒加载真实实现。
agent_stream = None


def _resolve_agent_stream():
    global agent_stream
    if agent_stream is not None:
        return agent_stream
    from agents.EDPAgent import agent_stream as edp_agent_stream

    agent_stream = edp_agent_stream
    return agent_stream


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
    sub_task_path: tuple[str, ...] = ()
    otel_session_id: str = ""   # 主 Agent 原始 conversation_id（子 Agent 场景，用于 span 的 session.id）


@dataclass
class _RunAgentOptions:
    """run_agent 的可选参数封装，避免相关参数散列扩散。"""

    step_counter: list[int] | None = None
    heartbeat_runtime: HeartbeatRuntimeManager | None = None


class Executor(AgentExecutor):
    """编排入口（薄封装，核心逻辑委托给 Route + State）

    职责：
    - 接收 Requester 请求，解析请求信息
    - 构建 NormalizedEvent，委托 RouteDispatcher.dispatch() 路由+分发
    - run_agent() 迭代 agent_stream，将每个事件转为 NormalizedEvent 后 dispatch
    - 不包含任何 if target.type 分支判断，所有业务逻辑由 handler 处理
    """

    def __init__(
        self,
        redis: RedisClient,
        route_dispatcher: RouteDispatcher | None = None,
        state_manager: TaskStateManager | None = None,
        *,
        va_client: Any | None = None,
        task_store: Any | None = None,
        sub_agent_client: Any | None = None,
        client_factory: Any | None = None,
        session_task_kv: Any | None = None,
        session_request_kv: Any | None = None,
        **handler_limits: Any,
    ) -> None:
        self._redis = redis
        self._heartbeat_interval_seconds = int(handler_limits.get("heartbeat_interval_seconds", 15))
        self._heartbeat_timeout_seconds = int(handler_limits.get("heartbeat_timeout_seconds", 1800))
        self._session_task_kv = session_task_kv
        self._session_request_kv = session_request_kv
        # 心跳 runtime 按会话复用，避免续轮时 normal loop 被上一轮 finally 提前销毁。
        self._heartbeat_runtime_registry: dict[str, HeartbeatRuntimeManager] = {}
        self._heartbeat_runtime_lock = asyncio.Lock()
        self._remote_handler = None
        if route_dispatcher is None or state_manager is None:
            from orchestrator.handlers.remote_agent_handler import RemoteAgentHandler
            from orchestrator.handlers.requester_handler import RequesterHandler

            state_manager = TaskStateManager(task_store=task_store)
            route_dispatcher = RouteDispatcher(state_manager)
            remote_handler = RemoteAgentHandler(
                va_client=va_client,
                redis=redis,
                state_manager=state_manager,
                client_factory=client_factory,
                heartbeat_interval_seconds=self._heartbeat_interval_seconds,
                heartbeat_timeout_seconds=self._heartbeat_timeout_seconds,
                max_concurrent_sub_agents=int(handler_limits.get("max_concurrent_sub_agents", 3)),
                sub_agent_timeout_seconds=int(handler_limits.get("sub_agent_timeout_seconds", 1800)),
                max_parallel_workflows_per_agent=int(handler_limits.get("max_parallel_workflows_per_agent", 3)),
                workflow_timeout_seconds=int(handler_limits.get("workflow_timeout_seconds", 900)),
                max_call_depth=int(handler_limits.get("max_call_depth", 3)),
            )
            if sub_agent_client is not None:
                remote_handler._sub_agent_clients[""] = sub_agent_client
            route_dispatcher.register_handler(
                "requester",
                RequesterHandler(state_manager).handle,
            )
            route_dispatcher.register_handler("remote_agent", remote_handler.handle)
            self._remote_handler = remote_handler

        self._route_dispatcher = route_dispatcher
        self._state_manager = state_manager

    async def _resolve_root_task_id(self, conv_id: str) -> str:
        """通过 conv_id 查 Redis 映射表获取 root_task_id"""
        conv_task_key = CONV_TASK_KEY.format(conv_id)
        root_task_id = self._redis.get(conv_task_key)
        if inspect.isawaitable(root_task_id):
            root_task_id = await root_task_id
        if isinstance(root_task_id, bytes):
            root_task_id = root_task_id.decode("utf-8")
        return root_task_id if isinstance(root_task_id, str) else ""

    async def _acquire_heartbeat_runtime(
        self,
        *,
        conv_id: str,
        task_id: str,
        event_queue: EventQueue,
    ) -> HeartbeatRuntimeManager:
        async with self._heartbeat_runtime_lock:
            runtime = self._heartbeat_runtime_registry.get(conv_id)
            if runtime is None:
                runtime = HeartbeatRuntimeManager(
                    conv_id=conv_id,
                    task_id=task_id,
                    event_queue=event_queue,
                    redis=self._redis,
                    interval_seconds=self._heartbeat_interval_seconds,
                    timeout_seconds=self._heartbeat_timeout_seconds,
                )
                self._heartbeat_runtime_registry[conv_id] = runtime
                return runtime

            runtime.bind_context(
                conv_id=conv_id,
                task_id=task_id,
                event_queue=event_queue,
            )
            return runtime

    async def _release_heartbeat_runtime(self, conv_id: str) -> None:
        if not conv_id:
            return
        async with self._heartbeat_runtime_lock:
            runtime = self._heartbeat_runtime_registry.pop(conv_id, None)
        if runtime is not None:
            await runtime.cleanup()

    async def _cleanup_heartbeat_runtime_if_terminal(
        self,
        *,
        conv_id: str,
        task_id: str,
        call_context: ServerCallContext,
    ) -> None:
        if not conv_id or not task_id:
            return
        try:
            task = await self._state_manager.get_task(task_id, call_context)
        except Exception as exc:
            logger.warning("[Executor] 查询任务状态失败，跳过心跳回收: {}", exc)
            return
        if not isinstance(task, dict):
            return
        status = str(task.get("status_state") or "").upper()
        if status in {"COMPLETED", "FAILED", "CANCELED"}:
            await self._release_heartbeat_runtime(conv_id)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """编排入口：解析请求 → 构建 NormalizedEvent → dispatch → return

        核心原则：execute 只做"构建事件 → dispatch"，不做任何路由目标判断。
        所有后续逻辑（创建 Task、续轮、委托调用等）由 handler 处理。
        """
        # 子 Agent 作为 A2A Server：提取主 Agent 传来的 traceparent + session_id。
        # attach 后，本 execute 内创建的所有 span（含 SDK 的 chain.EDPAgent）自动挂在主 Agent trace 树下。
        _sess_ctx = self._extract_session_context(context)
        upstream_traceparent = _sess_ctx.get("traceparent", "")
        upstream_session_id = _sess_ctx.get("session_id", "")
        _otel_token = None
        if upstream_traceparent:
            try:
                from opentelemetry.context import attach
                from opentelemetry.propagate import extract

                _otel_token = attach(extract({"traceparent": upstream_traceparent}))
            except ImportError:
                pass

        await self._init_session_context_if_needed(context)

        conv_id = context.context_id or ""
        task_id = context.task_id or str(uuid.uuid4())
        call_context = context.call_context
        current_task = context.current_task

        user_query, original_headers, original_body, sub_task_path = self._extract_request_info(context)

        root_task_id = await self._resolve_root_task_id(conv_id)
        # 兼容老版本：先按前端未传入指定task_id处理，后续再根据需求调整为指定task_id处理
        is_specify_task = False

        turn_ctx = _TurnContext(
            conv_id=conv_id,
            task_id=task_id,
            call_context=call_context,
            event_queue=event_queue,
            sub_task_path=sub_task_path,
            otel_session_id=upstream_session_id,
        )

        transient_hb = False
        if conv_id:
            heartbeat_runtime = await self._acquire_heartbeat_runtime(
                conv_id=conv_id,
                task_id=task_id,
                event_queue=event_queue,
            )
        else:
            # 无 conv_id 的极端场景退化为单轮 runtime，避免空键污染会话注册表。
            transient_hb = True
            heartbeat_runtime = HeartbeatRuntimeManager(
                conv_id=conv_id,
                task_id=task_id,
                event_queue=event_queue,
                redis=self._redis,
                interval_seconds=self._heartbeat_interval_seconds,
                timeout_seconds=self._heartbeat_timeout_seconds,
            )

        event = NormalizedEvent(
            type="request",
            data={
                "user_query": user_query,
                "headers": original_headers,
                "body": original_body,
            },
            metadata={"source": "requester", "task_id": task_id},
        )
        handler_context = {
            "task_id": task_id,
            "conv_id": conv_id,
            "root_task_id": root_task_id,
            "current_task": current_task,
            "call_context": call_context,
            "event_queue": event_queue,
            "turn_ctx": turn_ctx,
            "executor": self,
            "query": user_query,
            "original_body": original_body,
            "headers": original_headers,
            "step_counter": [0],
            "route_dispatcher": self._route_dispatcher,
            "is_specify_task": is_specify_task,
            "heartbeat_runtime": heartbeat_runtime,
            "otel_session_id": upstream_session_id,
        }
        try:
            await self._route_dispatcher.dispatch(event, handler_context)
        except Exception:
            if conv_id:
                await self._release_heartbeat_runtime(conv_id)
            raise
        finally:
            if transient_hb:
                await heartbeat_runtime.cleanup()
            else:
                await self._cleanup_heartbeat_runtime_if_terminal(
                    conv_id=conv_id,
                    task_id=task_id,
                    call_context=call_context,
                )
            # detach OTel context，防泄漏（异常路径也执行）
            if _otel_token is not None:
                try:
                    from opentelemetry.context import detach

                    detach(_otel_token)
                except ImportError:
                    pass

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        conv_id = context.context_id or ""
        await self._cancel_remote_children(conv_id)
        if task_id:
            await self._state_manager.update_task_status(
                task_id, "CANCELED", context.call_context,
            )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=conv_id,
                status=TaskStatus(state=TASK_STATE_CANCELED),
            )
        )
        await self._release_heartbeat_runtime(conv_id)
        logger.info(f"[Executor] cancel: conv={conv_id}, task={task_id}")

    async def cancel_task(self, conv_id: str, call_context: ServerCallContext | None = None) -> None:
        task_id = await self._resolve_root_task_id(conv_id)
        child_task_infos: list[dict[str, str]] = []
        if task_id:
            await self._state_manager.update_task_status(
                task_id, "CANCELED", call_context,
            )
            task = await self._state_manager.get_task(task_id, call_context)
            metadata = task.get("metadata", {}) if isinstance(task, dict) else {}
            for sub_task_id in metadata.get("sub_tasks", []) or []:
                child_task_infos.append({"task_id": str(sub_task_id), "url": ""})
        await self._cancel_remote_children(conv_id, child_task_infos)
        await self._release_heartbeat_runtime(conv_id)
        logger.info(f"[Executor] cancel_task: conv={conv_id}, task={task_id}")

    async def _cancel_remote_children(
        self,
        conv_id: str,
        child_task_infos: list[dict[str, str]] | None = None,
    ) -> None:
        remote_handler = self._remote_handler or getattr(self, "_test_remote_handler", None)
        if remote_handler is None and self._route_dispatcher is not None:
            get_instance = getattr(self._route_dispatcher, "get_handler_instance", None)
            if get_instance is not None:
                remote_handler = get_instance("remote_agent")
        if remote_handler is None:
            return
        await remote_handler.cancel_task(conv_id, child_task_infos)

    async def run_agent(
        self,
        turn_ctx: _TurnContext,
        query: str,
        original_body: dict,
        cascade_result: dict[str, Any] | None,
        run_options: _RunAgentOptions | dict[str, Any] | None = None,
    ) -> None:
        """Local Agent 执行：迭代 agent_stream，事件处理 + DelegateRequest 转 dispatch

        核心原则：run_agent 只做"迭代流 → 归一化 → dispatch"，不做业务判断。
        - 所有 agent event 统一归一化为 NormalizedEvent 后 dispatch
        - 具体处理逻辑（step 边界、a2a 转换、委托调用等）由对应 handler 负责
        - handler 通过 context["_stream_interrupted"] 信号通知流中断（如 DelegateRequest）
        - 正常结束 → finalize_completed
        """
        options = run_options
        if isinstance(options, dict):
            options = _RunAgentOptions(
                step_counter=options.get("step_counter") if isinstance(options.get("step_counter"), list) else None,
                heartbeat_runtime=options.get("heartbeat_runtime"),
            )
        elif options is None:
            options = _RunAgentOptions()

        step_counter = options.step_counter
        heartbeat_runtime = options.heartbeat_runtime
        if step_counter is None:
            step_counter = [0]

        conv_id = turn_ctx.conv_id
        task_id = turn_ctx.task_id
        call_context = turn_ctx.call_context
        event_queue = turn_ctx.event_queue

        root_task_id = await self._resolve_root_task_id(conv_id)

        turn_start = time.monotonic()
        is_cascade = cascade_result is not None
        logger.info(
            f"[Executor] run_agent 开始: conv={conv_id}, task={task_id}, "
            f"is_cascade={is_cascade}, step_counter={step_counter[0]}"
        )

        handler_context = {
            "task_id": task_id,
            "conv_id": conv_id,
            "root_task_id": root_task_id,
            "current_task": None,
            "call_context": call_context,
            "event_queue": event_queue,
            "turn_ctx": turn_ctx,
            "executor": self,
            "query": query,
            "original_body": original_body,
            "step_counter": step_counter,
            "route_dispatcher": self._route_dispatcher,
            "heartbeat_runtime": heartbeat_runtime,
        }

        event_count = 0
        final_answer_chunk = ""
        final_answer_end = ""
        stream_fn = _resolve_agent_stream()
        async for event in stream_fn(
            query=query,
            conv_id=conv_id,
            cascade_result=cascade_result,
            context={
                "body": original_body,
                "is_sub_agent": bool(turn_ctx.sub_task_path),
            },
        ):
            event_count += 1
            event_type = event.get("type") if isinstance(event, dict) else ""
            event_data = event.get("data", {}) if isinstance(event, dict) else {}
            if not isinstance(event_data, dict):
                event_data = {}
            content = str(event_data.get("content") or "")
            if event_type == "final_answer_chunk" and content:
                final_answer_chunk = content
            elif event_type == "final_answer_end" and content:
                final_answer_end = content
            logger.debug(
                f"[Executor] received agent event #{event_count}: "
                f"type={event_type}"
            )

            normalized = self._normalize_agent_event(event, task_id)
            await self._route_dispatcher.dispatch(normalized, handler_context)

            if handler_context.get("_stream_interrupted"):
                turn_duration_ms = (time.monotonic() - turn_start) * 1000
                logger.info(
                    f"[Executor] ⏱️ run_agent 返回: conv={conv_id}, "
                    f"duration={turn_duration_ms:.2f}ms, "
                    f"events_received={event_count}, steps={step_counter[0]}"
                )
                return

        await self._state_manager.finalize_completed(task_id, call_context)
        await event_queue.enqueue_event(
            self._build_completed_status_event(
                task_id,
                conv_id,
                final_answer_chunk or final_answer_end,
            )
        )
        clear_artifact_id_cache(task_id)

        turn_duration_ms = (time.monotonic() - turn_start) * 1000
        logger.info(
            f"[Executor] ⏱️ run_agent 正常结束: conv={conv_id}, "
            f"duration={turn_duration_ms:.2f}ms, "
            f"events_received={event_count}, steps_accumulated={step_counter[0]}"
        )

    @staticmethod
    def _build_completed_status_event(
        task_id: str,
        conv_id: str,
        content: str,
    ) -> TaskStatusUpdateEvent:
        msg = Message(
            role=ROLE_AGENT,
            message_id=str(uuid.uuid4()),
            task_id=task_id,
            context_id=conv_id,
            parts=[Part(text=content or "")],
        )
        return TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=conv_id,
            status=TaskStatus(state=TASK_STATE_COMPLETED, message=msg),
        )

    @staticmethod
    def _normalize_agent_event(event, task_id: str) -> NormalizedEvent:
        """将 agent_stream 产生的原始事件归一化为 NormalizedEvent

        归一化规则：
          - event.type 直接映射为 NormalizedEvent.type（如 "delegate"、"tool_start"、"think_start" 等）
          - data 统一携带 raw_event 保留原始事件对象，供 handler 按需提取字段
        归一化后统一交由 RouteDispatcher.dispatch 路由分发，
        具体处理逻辑在对应 handler 中完成。
        """
        if not isinstance(event, dict):
            event_type = getattr(event, "type", "")
            if hasattr(event, "model_dump"):
                data = event.model_dump(exclude={"type"}, exclude_none=True)
            elif hasattr(event, "dict"):
                data = event.dict(exclude={"type"}, exclude_none=True)
            else:
                data = {
                    key: value
                    for key, value in getattr(event, "__dict__", {}).items()
                    if key != "type" and value is not None
                }
            event = {"type": event_type, "data": data if isinstance(data, dict) else {}}
        data = event.get("data", {})
        if not isinstance(data, dict):
            raise TypeError("agent_stream event data must be a dict")
        log_channel_event(
            level=Level.DEBUG,
            action="EXECUTOR_NORMALIZE_AGENT_EVENT",
            event_type=str(event.get("type", "")),
            task_id=task_id,
            payload={"data_keys": sorted(data.keys()) if isinstance(data, dict) else []},
        )
        return NormalizedEvent(
            type=event.get("type", ""),
            data={"raw_event": event},
            metadata={"source": "local_agent", "task_id": task_id},
        )

    def _extract_session_context(self, context: RequestContext) -> dict:
        if not context.message:
            return {}
        for part in context.message.parts:
            if part.WhichOneof("content") != "data":
                continue
            data = MessageToDict(part.data)
            if isinstance(data, dict):
                session_ctx = data.get("session_context")
                if isinstance(session_ctx, dict):
                    return session_ctx
        return {}

    async def _init_session_context_if_needed(self, context: RequestContext) -> None:
        session_ctx = self._extract_session_context(context)
        if not session_ctx:
            return
        conv_id = context.context_id or ""
        if not conv_id:
            return
        existing = await self._redis.get_json(session_request_key(conv_id))
        if existing is not None:
            return
        # DB回源：Redis未命中时尝试从DB恢复首轮请求缓存
        if self._session_request_kv is not None:
            try:
                db_record = await self._session_request_kv.get(conv_id)
                if db_record is not None and isinstance(db_record, dict):
                    await self._redis.set_json(
                        session_request_key(conv_id),
                        db_record,
                        ex=_TTL,
                    )
                    return
            except Exception as e:
                logger.warning(f"[Executor] session_request DB回源失败: {e}")
        session_data = {
            "headers": session_ctx.get("headers", {}),
            "trace_id": session_ctx.get("trace_id", ""),
            "agent_id": getattr(get_settings(), "dpa_agent_id", "") or "",
            "params": session_ctx.get("params", {}),
            "body": session_ctx.get("body", {}),
            "session_id": session_ctx.get("session_id", ""),
            "traceparent": session_ctx.get("traceparent", ""),
        }
        if self._session_request_kv is not None:
            try:
                await self._session_request_kv.put(conv_id, session_data)
            except Exception as e:
                logger.warning(f"[Executor] session_request DB写入失败: {e}")
        await self._redis.set_json(
            session_request_key(conv_id),
            session_data,
            ex=_TTL,
        )
        logger.info(f"[Executor] sub-session context initialized: conv={conv_id}")

    def _extract_request_info(
        self, context: RequestContext
    ) -> tuple[str, dict, dict, tuple[str, ...]]:
        """从 RequestContext 中提取请求信息"""
        user_query = ""
        original_headers: dict = {}
        original_body: dict = {}
        sub_task_path: tuple[str, ...] = ()
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
                        session_ctx = data.get("session_context")
                        if isinstance(session_ctx, dict):
                            path = session_ctx.get("sub_task_path")
                            if isinstance(path, list):
                                sub_task_path = tuple(str(p) for p in path)
        return user_query, original_headers, original_body, sub_task_path
