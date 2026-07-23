from __future__ import annotations

import json
import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, TYPE_CHECKING

import httpx
from a2a.client import Client, ClientCallContext, ClientFactory
from a2a.client.errors import A2AClientError
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from a2a.utils.errors import TaskNotFoundError, UnsupportedOperationError
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    Message,
    Artifact,
    Part,
    GetTaskRequest,
    SendMessageRequest,
    SubscribeToTaskRequest,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    ROLE_AGENT,
    ROLE_USER,
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct, Value
from loguru import logger

from common.constants import session_request_key
from common.logger import (
    Extra,
    Tag,
    build_versatile_end_observation,
    build_versatile_start_observation,
    mask_sensitive_fields,
    to_logger,
    Level,
)
from common.redis_client import RedisClient
from config import get_settings
from channels.dict_to_a2a import dict_to_a2a
try:
    # 编排层 span 的 context manager 由 EDPAgent（otel_span_helper.py）提供，
    # 与其 chain/llm span 共用同一 TracerProvider；tracer 未注入
    # （OTel 关闭/未装）时 helper 自身 yield None，零侵入。
    from agents.EDPAgent.otel_span_helper import (
        start_sub_agent_dispatch_span,
        start_versatile_adapter_span,
    )
except Exception:  # EDPAgent 未提供 helper 时降级为空操作（不阻断服务）
    from contextlib import nullcontext

    def start_versatile_adapter_span(*_args, **_kwargs):
        return nullcontext(None)

    def start_sub_agent_dispatch_span(*_args, **_kwargs):
        return nullcontext(None)
from ..route.normalized_event import NormalizedEvent, RouteTarget
from ..state import (
    InputRequiredState,
    META_KEY_REMOTE_TASK_ID,
    META_KEY_SOURCE_AGENT,
    TaskStateManager,
)

if TYPE_CHECKING:
    from a2a.server.events import EventQueue
    from ..executor import _TurnContext


def _set_span_attrs(span, attrs: Dict[str, Any]) -> None:
    """在 yield 出的 span 上补 response 属性（空串/None 跳过；span None/非 recording 时 no-op）。"""
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    for key, value in (attrs or {}).items():
        if value is None or value == "":
            continue
        try:
            span.set_attribute(key, value)
        except Exception:
            # 属性类型不兼容等异常不阻断主流程
            pass


def _set_current_span_attrs(attrs: Dict[str, Any]) -> None:
    """在当前活跃 span 上补属性（_call_versatile_adapter 等 finally 块拿不到 span 对象时用）。

    span 由调用方 ``with`` 创建并设为 current，此处经 ``get_current_span()`` 取回。
    无当前 span / OTel 未装 → no-op。
    """
    try:
        from opentelemetry.trace import get_current_span
    except ImportError:
        return
    _set_span_attrs(get_current_span(), attrs)


def _raw_data(raw_event: Any) -> dict[str, Any]:
    if isinstance(raw_event, dict):
        data = raw_event.get("data", {})
        return data if isinstance(data, dict) else {}
    return {}


def _field(raw_event: Any, name: str, default: str = "") -> str:
    data = _raw_data(raw_event)
    value = data.get(name, getattr(raw_event, name, default))
    return str(value or default)


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
    # logger.opt(lazy=True).debug(
    #     "[Executor] [VersatileProxy] chunk #{} payload={}",
    #     lambda c=stream_resp_count: c,
    #     lambda e=event: _safe_dump_event(e),
    # )
    to_logger(
        level=Level.DEBUG,
        message={
            "stream_resp_count": stream_resp_count,
            "content": _safe_dump_event(event),
        },
        extra=Extra(tag=Tag.TAG_VERSATILE_CHUNK, cost=0),
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


@dataclass(frozen=True)
class _DriveSubAgentRequest:
    client: Client
    spec: dict[str, Any]
    query: str
    turn_ctx: Any
    path: list[str]
    cancel_event: asyncio.Event | None = None


@dataclass(frozen=True)
class _ContinueVaRequest:
    turn_ctx: Any
    va_task_id: str
    user_input: str
    headers: dict
    original_body: dict
    context: Optional[Dict[str, Any]] = None


class RemoteAgentHandler:
    """Remote Agent 目标处理器

    职责：
    - 处理 delegate 类型事件：委托调用 VersatileAdapter，根据返回决定续轮或挂起
    - 处理 request 类型事件：INPUT_REQUIRED 续轮调用 VersatileAdapter，恢复远程调用
    - 包含 VA 调用的完整逻辑（_call_versatile_adapter / _continue_versatile_adapter）
    """

    def __init__(
        self,
        va_client: Client,
        redis: RedisClient,
        state_manager: TaskStateManager,
        client_factory: ClientFactory | None = None,
        heartbeat_interval_seconds: int = 15,
        heartbeat_timeout_seconds: int = 1800,
        max_concurrent_sub_agents: int = 3,
        sub_agent_timeout_seconds: int = 1800,
        max_parallel_workflows_per_agent: int = 3,
        workflow_timeout_seconds: int = 900,
        max_call_depth: int = 3,
        session_request_kv: object | None = None,
    ):
        self._va_client = va_client
        self._redis = redis
        self._state_manager = state_manager
        self._client_factory = client_factory
        self.heartbeat_interval_seconds = int(heartbeat_interval_seconds)
        self.heartbeat_timeout_seconds = int(heartbeat_timeout_seconds)
        self._session_request_kv = session_request_kv
        self._sub_agent_clients: dict[str, Client] = {}
        self.max_concurrent_sub_agents = max_concurrent_sub_agents
        self.sub_agent_timeout_seconds = sub_agent_timeout_seconds
        self.max_parallel_workflows_per_agent = max_parallel_workflows_per_agent
        self.workflow_timeout_seconds = workflow_timeout_seconds
        self.max_call_depth = max_call_depth
        self._cancel_tokens: dict[str, asyncio.Event] = {}
        self._child_task_ids: dict[str, dict[str, dict[str, str]]] = {}

    async def handle(
        self,
        event: NormalizedEvent,
        target: RouteTarget,
        context: Dict[str, Any],
    ) -> None:
        event_type = event.type
        event_data = event.data

        if event_type == "delegate":
            await self._emit_delegate_step_boundary(event_data, context)
            await self._handle_delegate(event_data, context)
            context["_stream_interrupted"] = True
        elif event_type == "multi_delegate":
            await self._handle_multi_delegate(event_data, context)
            context["_stream_interrupted"] = True
        elif event_type == "sub_agent_dispatch":
            await self._handle_sub_agent_dispatch(event_data, context)
            context["_stream_interrupted"] = True
        elif event_type == "request":
            await self._handle_request_resume(event_data, target, context)
        else:
            logger.warning(
                f"[RemoteAgentHandler] 未知事件类型: {event_type}"
            )

    async def _emit_delegate_step_boundary(
        self,
        event_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        step_counter = context.get("step_counter")
        turn_ctx = context.get("turn_ctx")
        task_id = context.get("task_id", "")
        conv_id = context.get("conv_id", "")
        event_queue = turn_ctx.event_queue if turn_ctx else None

        if not isinstance(step_counter, list):
            step_counter = [0]
        if not step_counter:
            step_counter.append(0)

        if step_counter is not None:
            step_counter[0] += 1

        delegate = event_data.get("raw_event")
        desc = _field(delegate, "task_description") if delegate else ""
        intent = _field(delegate, "intent") if delegate else ""

        planning_content = (
            f"[执行轨迹] 正在执行步骤{step_counter[0]}: "
            f"{desc} "
            f"(tool=adapter:versatile_proxy)"
        )
        planning = {
            "type": "planning_execution_process",
            "data": {"content": planning_content},
        }
        planning_a2a = dict_to_a2a(planning, task_id, conv_id)
        if planning_a2a is not None and event_queue is not None:
            await event_queue.enqueue_event(planning_a2a)

        logger.info(
            f"[RemoteAgentHandler] step 边界: 步骤{step_counter[0]} "
            f"(tool=adapter:versatile_proxy, intent={intent})"
        )
        logger.info(
            f"[RemoteAgentHandler] DelegateRequest → {intent}: "
            f"{desc!r:.60}"
        )

    async def _handle_delegate(
        self,
        event_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        turn_ctx = context.get("turn_ctx")
        delegate = event_data.get("raw_event")

        if turn_ctx is None or delegate is None:
            logger.warning("[RemoteAgentHandler] delegate 处理缺少必要上下文")
            return

        with start_versatile_adapter_span(
            query_intent=_field(delegate, "intent"),
            query_description=_field(delegate, "task_description"),
            session_id=turn_ctx.otel_session_id or turn_ctx.conv_id,
            dispatch_mode="single",
        ):
            va_result, va_task_id, finalized = await self._call_versatile_adapter(
                turn_ctx,
                delegate=delegate,
                context=context,
            )

        parent_task_id = turn_ctx.task_id
        if va_task_id and parent_task_id:
            await self._state_manager.add_sub_task(
                parent_task_id, va_task_id, turn_ctx.call_context,
            )
            logger.info(
                f"[RemoteAgentHandler] 子任务已注册: parent={parent_task_id}, "
                f"sub_task={va_task_id}"
            )

        if va_result is not None:
            executor = context.get("executor")
            if executor is None:
                logger.warning("[RemoteAgentHandler] 缺少 executor，跳过 cascade 续轮")
                return
            query = context.get("query", "")
            original_body = context.get("original_body", {})
            step_counter = context.get("step_counter")

            await self._state_manager.update_task_status(
                turn_ctx.task_id, "WORKING", turn_ctx.call_context,
            )

            await executor.run_agent(
                turn_ctx,
                query=query,
                original_body=original_body,
                cascade_result=va_result,
                run_options={
                    "step_counter": step_counter,
                    "heartbeat_runtime": context.get("heartbeat_runtime"),
                },
            )
        elif finalized:
            pass
        else:
            await self._suspend_task(turn_ctx, va_task_id, context)

    async def _handle_multi_delegate(
        self,
        event_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        turn_ctx = context.get("turn_ctx")
        raw_event = event_data.get("raw_event") or {}
        workflows = _raw_data(raw_event).get("workflows", [])
        if turn_ctx is None or not isinstance(workflows, list):
            logger.warning("[RemoteAgentHandler] multi_delegate missing context/workflows")
            return

        cancel_event = self._cancel_tokens.setdefault(turn_ctx.conv_id, asyncio.Event())
        try:
            if len(workflows) > self.max_parallel_workflows_per_agent:
                err = f"parallel workflow limit exceeded: {len(workflows)} > {self.max_parallel_workflows_per_agent}"
                cascade = {
                    "workflow_results": [
                        {
                            "workflow_id": str((w or {}).get("workflow_id", "")),
                            "status": "failed",
                            "result": None,
                            "error": err,
                            "elapsed_ms": 0,
                        }
                        for w in workflows
                        if isinstance(w, dict)
                    ]
                }
            else:
                results = await asyncio.gather(
                    *[
                        self._run_one_workflow(w, turn_ctx, cancel_event)
                        for w in workflows
                        if isinstance(w, dict)
                    ],
                    return_exceptions=True,
                )
                cascade = {
                    "workflow_results": [
                        r if isinstance(r, dict) else {
                            "workflow_id": "",
                            "status": "failed",
                            "result": None,
                            "error": str(r),
                            "elapsed_ms": 0,
                        }
                        for r in results
                    ]
                }
        finally:
            self._cancel_tokens.pop(turn_ctx.conv_id, None)

        executor = context.get("executor")
        if executor is not None:
            await executor.run_agent(
                turn_ctx,
                query=context.get("query", ""),
                original_body=context.get("original_body", {}),
                cascade_result=cascade,
                run_options={
                    "step_counter": context.get("step_counter"),
                    "heartbeat_runtime": context.get("heartbeat_runtime"),
                },
            )

    async def _handle_sub_agent_dispatch(
        self,
        event_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        turn_ctx = context.get("turn_ctx")
        raw_event = event_data.get("raw_event") or {}
        specs = _raw_data(raw_event).get("specs", [])
        if turn_ctx is None or not isinstance(specs, list):
            logger.warning("[RemoteAgentHandler] sub_agent_dispatch missing context/specs")
            return

        valid_specs = [s for s in specs if isinstance(s, dict)]
        self_depth = len(getattr(turn_ctx, "sub_task_path", ()) or ())
        if self_depth + 1 > self.max_call_depth:
            skipped = [
                {
                    "entity_id": str(s.get("entity_id", "")),
                    "entity_name": str(s.get("entity_name", "")),
                    "reason": "max_call_depth",
                }
                for s in valid_specs
            ]
            results = []
        else:
            dispatched = valid_specs[: self.max_concurrent_sub_agents]
            skipped = [
                {
                    "entity_id": str(s.get("entity_id", "")),
                    "entity_name": str(s.get("entity_name", "")),
                    "reason": "concurrency_limit",
                }
                for s in valid_specs[self.max_concurrent_sub_agents:]
            ]
            cancel_event = self._cancel_tokens.setdefault(turn_ctx.conv_id, asyncio.Event())
            self._child_task_ids.setdefault(turn_ctx.conv_id, {})
            try:
                results = await asyncio.gather(
                    *[self._run_one_sub_agent(s, turn_ctx, cancel_event) for s in dispatched],
                    return_exceptions=True,
                )
            finally:
                self._cancel_tokens.pop(turn_ctx.conv_id, None)
                self._child_task_ids.pop(turn_ctx.conv_id, None)
        cascade = {
            "sub_agent_results": [
                r if isinstance(r, dict) else {
                    "entity_id": "",
                    "status": "failed",
                    "content": "",
                    "error": str(r),
                    "child_task_id": "",
                }
                for r in results
            ],
            "skipped_entities": skipped,
            "concurrency_limit": self.max_concurrent_sub_agents,
        }

        executor = context.get("executor")
        if executor is not None:
            await executor.run_agent(
                turn_ctx,
                query=context.get("query", ""),
                original_body=context.get("original_body", {}),
                cascade_result=cascade,
                run_options={
                    "step_counter": context.get("step_counter"),
                    "heartbeat_runtime": context.get("heartbeat_runtime"),
                },
            )

    async def _handle_request_resume(
        self,
        event_data: Dict[str, Any],
        target: RouteTarget,
        context: Dict[str, Any],
    ) -> None:
        turn_ctx = context.get("turn_ctx")
        current_task = context.get("current_task")
        user_input = event_data.get("user_input", context.get("query", ""))
        headers = event_data.get("headers", context.get("headers", {}))
        body = event_data.get("body", context.get("original_body", {}))

        if turn_ctx is None:
            logger.warning("[RemoteAgentHandler] request resume 处理缺少必要上下文")
            return

        if current_task is None:
            logger.warning(
                f"[RemoteAgentHandler] _handle_request_resume: current_task 为空, "
                f"conv={turn_ctx.conv_id}"
            )
            return

        meta = MessageToDict(current_task.metadata)
        va_task_id = meta.get(META_KEY_REMOTE_TASK_ID, meta.get("va_task_id", ""))
        source_agent = meta.get(META_KEY_SOURCE_AGENT, "")

        agent_key = target.agent_key or source_agent or "versatile_adapter"
        logger.info(
            f"[RemoteAgentHandler] 续轮路由到 Remote Agent: conv={turn_ctx.conv_id}, "
            f"task={turn_ctx.task_id}, agent_key={agent_key}, "
            f"remote_task_id={va_task_id}, source_agent={source_agent}"
        )

        with start_versatile_adapter_span(
            query_intent="",
            query_description="",
            session_id=turn_ctx.otel_session_id or turn_ctx.conv_id,
            dispatch_mode="single",
        ):
            await self._continue_versatile_adapter(
                _ContinueVaRequest(
                    turn_ctx=turn_ctx,
                    va_task_id=va_task_id,
                    user_input=user_input,
                    headers=headers,
                    original_body=body,
                    context=context,
                )
            )

    async def _suspend_task(
        self,
        turn_ctx: Any,
        va_task_id: Optional[str],
        context: Dict[str, Any],
    ) -> None:
        task_id = turn_ctx.task_id
        conv_id = turn_ctx.conv_id
        call_context = turn_ctx.call_context
        event_queue = turn_ctx.event_queue

        await self._state_manager.save_input_required(
            InputRequiredState(
                task_id=task_id,
                call_context=call_context,
                remote_task_id=va_task_id or "",
                workflow_id=None,
            )
        )

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=conv_id,
                status=TaskStatus(state=TASK_STATE_INPUT_REQUIRED),
            )
        )
        logger.info(
            f"[RemoteAgentHandler] VA 挂起：conv={conv_id}, va_task={va_task_id}"
        )

    async def _emit_sub_task(
        self,
        turn_ctx: Any,
        sub_task_path: list[str],
        node_kind: str,
        data: dict[str, Any],
    ) -> None:
        event = {
            "type": "sub_task",
            "data": {
                "sub_task_path": [str(p) for p in sub_task_path],
                "node_kind": node_kind,
                "data": data,
            },
        }
        logger.info(
            f"[RemoteAgentHandler] SubTaskEvent 盖章: sub_task_path={sub_task_path}, "
            f"node_kind={node_kind}, data={str(data)[:200]}"
        )
        await turn_ctx.event_queue.enqueue_event(
            dict_to_a2a(event, turn_ctx.task_id, turn_ctx.conv_id)
        )

    async def _run_one_workflow(
        self,
        workflow: dict[str, Any],
        turn_ctx: Any,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        workflow_id = str(workflow.get("workflow_id") or workflow.get("id") or uuid.uuid4())
        path = [str(p) for p in getattr(turn_ctx, "sub_task_path", ())] + [workflow_id]
        started = time.monotonic()
        if cancel_event is None:
            cancel_event = self._cancel_tokens.get(turn_ctx.conv_id)
        await self._emit_sub_task(
            turn_ctx,
            path,
            "workflow",
            {"event": "node_start", "intent": str(workflow.get("intent") or "")},
        )
        delegate = {
            "type": "delegate",
            "data": {
                "intent": workflow.get("intent") or workflow.get("query_intent") or "",
                "task_description": workflow.get("task_description") or workflow.get("query_description") or "",
                "target_agent": workflow.get("target_agent") or None,
            },
        }
        try:
            if cancel_event is not None and cancel_event.is_set():
                await self._emit_sub_task(
                    turn_ctx,
                    path,
                    "workflow",
                    {"event": "node_end", "status": "cancelled", "error": "workflow cancelled"},
                )
                return {
                    "workflow_id": workflow_id,
                    "status": "cancelled",
                    "result": None,
                    "error": "workflow cancelled",
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            with start_versatile_adapter_span(
                query_intent=str(delegate.get("data", {}).get("intent", "")),
                query_description=str(delegate.get("data", {}).get("task_description", "")),
                session_id=turn_ctx.otel_session_id or turn_ctx.conv_id,
                dispatch_mode="parallel",
                workflow_id=workflow_id,
                target_agent=str(delegate.get("data", {}).get("target_agent") or ""),
                sub_task_path=str(path),
            ) as wf_span:
                cascade = await asyncio.wait_for(
                    self._drive_workflow_va(turn_ctx, delegate, path, cancel_event),
                    timeout=self.workflow_timeout_seconds,
                )
                _set_span_attrs(wf_span, {
                    "openjiuwen.va.status": "done" if cascade is not None else "failed",
                    "openjiuwen.va.elapsed_ms": int((time.monotonic() - started) * 1000),
                    "openjiuwen.va.workflow_result": json.dumps(cascade, ensure_ascii=False) if cascade else "",
                })
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if cancel_event is not None and cancel_event.is_set():
                await self._emit_sub_task(
                    turn_ctx,
                    path,
                    "workflow",
                    {
                        "event": "node_end",
                        "status": "cancelled",
                        "error": "workflow cancelled",
                        "elapsed_ms": elapsed_ms,
                    },
                )
                return {
                    "workflow_id": workflow_id,
                    "status": "cancelled",
                    "result": None,
                    "error": "workflow cancelled",
                    "elapsed_ms": elapsed_ms,
                }
            if cascade is None:
                await self._emit_sub_task(
                    turn_ctx,
                    path,
                    "workflow",
                    {"event": "node_end", "status": "failed", "error": "VA 工作流未返回终态结果", "elapsed_ms": elapsed_ms},
                )
                return {
                    "workflow_id": workflow_id,
                    "status": "failed",
                    "result": None,
                    "error": "VA 工作流未返回终态结果",
                    "elapsed_ms": elapsed_ms,
                }
            await self._emit_sub_task(
                turn_ctx,
                path,
                "workflow",
                {"event": "node_end", "status": "done", "result": cascade, "elapsed_ms": elapsed_ms},
            )
            return {
                "workflow_id": workflow_id,
                "status": "done",
                "result": cascade,
                "error": "",
                "elapsed_ms": elapsed_ms,
            }
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            await self._emit_sub_task(
                turn_ctx,
                path,
                "workflow",
                {"event": "node_end", "status": "timeout", "error": "workflow timeout", "elapsed_ms": elapsed_ms},
            )
            return {
                "workflow_id": workflow_id,
                "status": "timeout",
                "result": None,
                "error": "workflow timeout",
                "elapsed_ms": elapsed_ms,
            }
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            await self._emit_sub_task(
                turn_ctx,
                path,
                "workflow",
                {"event": "node_end", "status": "failed", "error": str(exc), "elapsed_ms": elapsed_ms},
            )
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "result": None,
                "error": str(exc),
                "elapsed_ms": elapsed_ms,
            }

    async def _get_sub_agent_client(
        self, url: str, agent_card_name: str = "SubDPA"
    ) -> Optional[Client]:
        cache_key = f"{url}|{agent_card_name}"
        client = self._sub_agent_clients.get(cache_key)
        if client is not None:
            return client
        if not url or self._client_factory is None:
            return None
        client = self._client_factory.create(
            self._build_sub_agent_card(url, agent_card_name)
        )
        self._sub_agent_clients[cache_key] = client
        return client

    @staticmethod
    def _build_sub_agent_card(
        url: str, agent_card_name: str = "SubDPA"
    ) -> AgentCard:
        rpc_url = url.rstrip("/")
        card = AgentCard(name=agent_card_name, description="子 Agent A2A 端点", version="1.0.0")
        card.supported_interfaces.append(
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url=rpc_url,
                protocol_version=PROTOCOL_VERSION_1_0,
            )
        )
        card.capabilities.CopyFrom(AgentCapabilities(streaming=True))
        return card

    async def _drive_workflow_va(
        self,
        turn_ctx: Any,
        delegate: dict[str, Any],
        path: list[str],
        cancel_event: asyncio.Event | None,
    ) -> dict[str, Any]:
        conv_id = turn_ctx.conv_id
        cached = await self._redis.get_json(session_request_key(conv_id)) or {}
        headers = cached.get("headers", {})
        body = dict(cached.get("body", {}))
        params = cached.get("params", {})
        trace_id = cached.get("trace_id", "")

        intent = _field(delegate, "intent")
        task_description = _field(delegate, "task_description")
        target_agent = _field(delegate, "target_agent")
        effective_intent, effective_query = _rewrite_recommend_delegate(intent, task_description)

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

        request = self._build_va_message(
            _VaRequestPayload(
                query=effective_query,
                headers=headers,
                body=body,
                params=params,
                task_id="",
                conv_id=conv_id,
                trace_id=trace_id,
                agent_id=target_agent,
                target={"type": "workflow", "intent": effective_intent} if effective_intent else None,
            )
        )

        stream = self._va_client.send_message(request)
        final_result: Optional[dict[str, Any]] = None
        async for stream_resp in stream:
            if cancel_event is not None and cancel_event.is_set():
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()
                break
            event = self._parse_stream_event(stream_resp)
            if event is None:
                continue
            _log_va_chunk_debug(0, event)
            if isinstance(event, TaskArtifactUpdateEvent):
                err = self._extract_upstream_error(event)
                if err is not None:
                    raise RuntimeError(self._format_upstream_error(err))
                frame = self._artifact_to_dict(event)
                if frame is not None:
                    await self._emit_sub_task(turn_ctx, path, "workflow", frame)
            elif isinstance(event, TaskStatusUpdateEvent):
                state = event.status.state
                if state == TASK_STATE_COMPLETED:
                    workflow_result = self._extract_workflow_result(event)
                    final_result = (
                        {"workflow_result": workflow_result}
                        if workflow_result is not None
                        else {}
                    )
                    break
                if state == TASK_STATE_FAILED:
                    err = self._extract_failed_error(event) or {
                        "message": self._status_text(event) or "VA workflow failed"
                    }
                    raise RuntimeError(self._format_upstream_error(err))
                if state == TASK_STATE_INPUT_REQUIRED:
                    raise RuntimeError(
                        "VA workflow returned INPUT_REQUIRED; parallel workflow does not support suspension"
                    )
        return final_result if final_result is not None else {}

    async def _run_one_sub_agent(
        self,
        spec: dict[str, Any],
        turn_ctx: Any,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        entity_id = str(spec.get("entity_id") or uuid.uuid4())
        entity_name = str(spec.get("entity_name") or "")
        query = str(spec.get("query") or "")
        agent_card_name = str(spec.get("agent_card_name") or spec.get("name") or "SubDPA")
        endpoint_type = str(spec.get("endpoint_type") or "direct").lower()
        if endpoint_type == "gateway":
            gateway_base = str(spec.get("a2a_gateway_base") or "")
            url = f"{gateway_base}/a2a/{agent_card_name}" if gateway_base else ""
            logger.info(
                f"[SubAgent] entity_id={entity_id}, endpoint_type=gateway, "
                f"agent_card_name={agent_card_name}, url={url}"
            )
        else:
            url = str(spec.get("sub_agent_url") or spec.get("url") or "")
            logger.info(
                f"[SubAgent] entity_id={entity_id}, endpoint_type=direct, url={url}"
            )
        path = [str(p) for p in getattr(turn_ctx, "sub_task_path", ())] + [entity_id]
        started = time.monotonic()
        await self._emit_sub_task(
            turn_ctx,
            path,
            "agent",
            {"event": "node_start", "entity_name": entity_name},
        )
        try:
            client = await self._get_sub_agent_client(url, agent_card_name)
            if client is None:
                raise RuntimeError(f"sub agent url is missing or client factory unavailable: {url!r}")
            with start_sub_agent_dispatch_span(
                entity_id=entity_id,
                entity_name=entity_name,
                query=query,
                sub_agent_url=url,
                sub_task_path=str(path),
                context_id=f"{turn_ctx.conv_id}-sub-{entity_id}",
                session_id=turn_ctx.otel_session_id or turn_ctx.conv_id,
            ) as sub_span:
                result = await asyncio.wait_for(
                    self._drive_sub_agent(
                        _DriveSubAgentRequest(
                            client=client,
                            spec=spec,
                            query=query,
                            turn_ctx=turn_ctx,
                            path=path,
                            cancel_event=cancel_event,
                        )
                    ),
                    timeout=self.sub_agent_timeout_seconds,
                )
                _set_span_attrs(sub_span, {
                    "openjiuwen.subagent.child_task_id": result.get("child_task_id", ""),
                    "openjiuwen.subagent.status": "cancelled" if result.get("terminal_status") == "cancelled" else "done",
                    "openjiuwen.subagent.elapsed_ms": int((time.monotonic() - started) * 1000),
                    "openjiuwen.subagent.content_summary": str(result.get("content", ""))[:200],
                })
            if result.get("terminal_status") == "cancelled":
                await self._emit_sub_task(
                    turn_ctx,
                    path,
                    "agent",
                    {"event": "node_end", "status": "cancelled", "reason": "用户取消"},
                )
                return {
                    "entity_id": entity_id,
                    "status": "cancelled",
                    "content": "",
                    "error": "sub agent cancelled",
                    "child_task_id": result.get("child_task_id", ""),
                }
            if cancel_event is not None and cancel_event.is_set():
                await self._emit_sub_task(
                    turn_ctx,
                    path,
                    "agent",
                    {"event": "node_end", "status": "cancelled", "reason": "用户取消"},
                )
                return {
                    "entity_id": entity_id,
                    "status": "cancelled",
                    "content": "",
                    "error": "sub agent cancelled",
                    "child_task_id": result.get("child_task_id", ""),
                }
            await self._emit_sub_task(
                turn_ctx,
                path,
                "agent",
                {"event": "node_end", "status": "done", "content": result.get("content", "")},
            )
            return {
                "entity_id": entity_id,
                "status": "done",
                "content": result.get("content", ""),
                "error": "",
                "child_task_id": result.get("child_task_id", ""),
            }
        except asyncio.TimeoutError:
            await self._emit_sub_task(
                turn_ctx,
                path,
                "agent",
                {"event": "node_end", "status": "timeout", "error": "sub agent timeout"},
            )
            return {
                "entity_id": entity_id,
                "status": "timeout",
                "content": "",
                "error": "sub agent timeout",
                "child_task_id": "",
            }
        except Exception as exc:
            await self._emit_sub_task(
                turn_ctx,
                path,
                "agent",
                {"event": "node_end", "status": "failed", "error": str(exc)},
            )
            return {"entity_id": entity_id, "status": "failed", "content": "", "error": str(exc), "child_task_id": ""}

    async def _drive_sub_agent(self, request: _DriveSubAgentRequest) -> dict[str, Any]:
        client = request.client
        spec = request.spec
        query = request.query
        turn_ctx = request.turn_ctx
        path = request.path
        cancel_event = request.cancel_event

        data_struct = Struct()
        cached = await self._redis.get_json(session_request_key(turn_ctx.conv_id)) or {}
        # inject OTel W3C trace context：让子 Agent 的 span 挂在主 Agent trace 树下。
        # inject() 取当前 OTel context（此刻 current span = 本 sub_agent.dispatch）→ traceparent 以其为 parent。
        _traceparent = ""
        try:
            from opentelemetry.propagate import inject as _otel_inject

            _carrier: dict = {}
            _otel_inject(_carrier)
            _traceparent = _carrier.get("traceparent", "")
        except ImportError:
            pass
        data_struct.update(
            {
                "session_context": {
                    "headers": cached.get("headers", {}),
                    "params": cached.get("params", {}),
                    "trace_id": cached.get("trace_id", ""),
                    "body": cached.get("body", {}),
                    "sub_task_path": path,
                    "session_id": getattr(turn_ctx, "otel_session_id", "") or turn_ctx.conv_id,
                    "traceparent": _traceparent,
                }
            }
        )
        data_value = Value()
        data_value.struct_value.CopyFrom(data_struct)
        msg = Message(
            role=ROLE_USER,
            message_id=str(uuid.uuid4()),
            task_id="",
            context_id=f"{turn_ctx.conv_id}-sub-{spec.get('entity_id', '')}",
        )
        msg.parts.extend([Part(text=query), Part(data=data_value)])
        request = SendMessageRequest(message=msg)

        # 按 endpoint_type 决定是否注入 userId + token HTTP Header
        endpoint_type = str(spec.get("endpoint_type") or "direct").lower()
        call_context = None
        if endpoint_type == "gateway":
            upstream_headers = cached.get("headers", {})
            user_id = (
                upstream_headers.get("x-user-id")
                or upstream_headers.get("X-User-Id")
                or upstream_headers.get("userId")
                or upstream_headers.get("userid")
                or ""
            )
            ctx_headers: dict[str, str] = {}
            if user_id:
                ctx_headers["userId"] = user_id
            else:
                logger.warning("[SubAgent] userId 未从 session 缓存中提取到，A2AGateway 可能返回 401")
            token = str(spec.get("token") or "")
            if token:
                ctx_headers["token"] = token
            if ctx_headers:
                call_context = ClientCallContext(state={"headers": ctx_headers})
                logger.info(
                    f"[SubAgent] Header 注入: endpoint_type=gateway, "
                    f"userId={'有' if user_id else '无'}, token={'有' if token else '无'}"
                )

        content = ""
        child_task_id = ""
        retry_count = 0
        max_retries = 3

        while True:
            try:
                stream = (
                    client.send_message(request, context=call_context)
                    if not child_task_id
                    else client.subscribe(SubscribeToTaskRequest(id=child_task_id), context=call_context)
                )
                async for stream_resp in stream:
                    retry_count = 0
                    if cancel_event is not None and cancel_event.is_set():
                        return {"content": content, "child_task_id": child_task_id}
                    if stream_resp.HasField("task"):
                        if not child_task_id:
                            child_task_id = stream_resp.task.id
                            self._register_child_task(turn_ctx.conv_id, spec, child_task_id)
                        continue
                    event = self._parse_stream_event(stream_resp)
                    if isinstance(event, TaskArtifactUpdateEvent):
                        frame = self._artifact_to_dict(event)
                        if frame:
                            if frame.get("type") == "sub_task":
                                await self._emit_sub_task(
                                    turn_ctx,
                                    frame.get("sub_task_path") or path,
                                    frame.get("node_kind") or "agent",
                                    frame.get("data") or {},
                                )
                            else:
                                await self._emit_sub_task(turn_ctx, path, "agent", frame)
                    elif isinstance(event, TaskStatusUpdateEvent):
                        if event.status.state == TASK_STATE_COMPLETED:
                            content = self._status_text(event)
                            return {"content": content, "child_task_id": child_task_id}
                        if event.status.state == TASK_STATE_FAILED:
                            raise RuntimeError(self._status_text(event) or "sub agent failed")
                        if event.status.state == TASK_STATE_CANCELED:
                            return {"content": content, "child_task_id": child_task_id, "terminal_status": "cancelled"}
                return {"content": content, "child_task_id": child_task_id}
            except (UnsupportedOperationError, TaskNotFoundError):
                final = await self._fetch_final_via_get(child_task_id, client)
                if final is None:
                    raise
                content = str(final.get("content") or "")
                return {"content": content, "child_task_id": child_task_id}
            except (httpx.RemoteProtocolError, httpx.ReadError, OSError, A2AClientError) as exc:
                if isinstance(exc, A2AClientError) and not isinstance(
                    getattr(exc, "__cause__", None), (httpx.RequestError, OSError)
                ):
                    raise
                if not child_task_id:
                    raise
                if retry_count >= max_retries:
                    final = await self._fetch_final_via_get(child_task_id, client)
                    if final is None:
                        raise exc
                    content = str(final.get("content") or "")
                    return {"content": content, "child_task_id": child_task_id}
                retry_count += 1
                logger.warning(
                    f"[RemoteAgentHandler] sub agent SSE disconnected, "
                    f"resubscribe #{retry_count}: child={child_task_id}, {exc!r}"
                )
                await asyncio.sleep(2 ** retry_count)
        return {"content": content, "child_task_id": child_task_id}

    def _register_child_task(
        self,
        conv_id: str,
        spec: dict[str, Any],
        child_task_id: str,
    ) -> None:
        if not child_task_id:
            return
        entity_id = str(spec.get("entity_id") or child_task_id)
        url = str(spec.get("sub_agent_url") or spec.get("url") or "")
        self._child_task_ids.setdefault(conv_id, {})[entity_id] = {
            "task_id": child_task_id,
            "url": url,
        }

    async def cancel_task(
        self,
        conv_id: str,
        child_task_infos: list[dict[str, str]] | None = None,
    ) -> None:
        self._cancel_tokens.setdefault(conv_id, asyncio.Event()).set()
        for info in child_task_infos or []:
            task_id = str(info.get("task_id") or "")
            if not task_id:
                continue
            self._child_task_ids.setdefault(conv_id, {}).setdefault(
                task_id,
                {
                    "task_id": task_id,
                    "url": str(info.get("url") or ""),
                },
            )

        children = list(self._child_task_ids.get(conv_id, {}).values())
        for child in children:
            task_id = child.get("task_id", "")
            if not task_id:
                continue
            try:
                client = await self._get_sub_agent_client(child.get("url", ""))
                if client is None:
                    continue
                await client.cancel_task(CancelTaskRequest(id=task_id))
                logger.info(
                    f"[RemoteAgentHandler] child cancel sent: conv={conv_id}, child_task={task_id}"
                )
            except Exception as exc:
                logger.warning(
                    f"[RemoteAgentHandler] child cancel failed: conv={conv_id}, "
                    f"child_task={task_id}, error={exc}"
                )

    @staticmethod
    def _build_va_message(payload: _VaRequestPayload) -> SendMessageRequest:
        text_part = Part()
        text_part.text = payload.query

        data_struct = Struct()
        target = dict(payload.target or {})
        if payload.conv_id:
            target.setdefault("conversation_id", payload.conv_id)
        envelope = {
            "headers": payload.headers,
            "body": payload.body,
            "params": payload.params or {},
            "trace_id": payload.trace_id,
            "agent_id": payload.agent_id,
        }
        if target:
            envelope["target"] = target
        data_struct.update(
            envelope
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

    @staticmethod
    def _parse_stream_event(stream_resp):
        if stream_resp.HasField("artifact_update"):
            return stream_resp.artifact_update
        if stream_resp.HasField("status_update"):
            return stream_resp.status_update
        return None

    @staticmethod
    def _artifact_to_dict(event: TaskArtifactUpdateEvent) -> Optional[dict[str, Any]]:
        for part in event.artifact.parts:
            if part.WhichOneof("content") == "data":
                frame = MessageToDict(part.data)
                return frame if isinstance(frame, dict) else None
            if part.WhichOneof("content") == "text":
                return {"type": "thought", "content": part.text or ""}
        return None

    @staticmethod
    def _status_text(event: TaskStatusUpdateEvent) -> str:
        if not event.status or not event.status.message:
            return ""
        chunks: list[str] = []
        for part in event.status.message.parts:
            if part.WhichOneof("content") == "text" and part.text:
                chunks.append(part.text)
        return "".join(chunks)

    async def _fetch_final_via_get(self, task_id: str, client: Client | None) -> Optional[dict[str, Any]]:
        if not task_id or client is None:
            return None
        task = await client.get_task(GetTaskRequest(id=task_id))
        if task is None or not task.status or task.status.state != TASK_STATE_COMPLETED:
            return None
        msg = task.status.message
        if not msg:
            return None
        content = ""
        for part in msg.parts:
            if part.WhichOneof("content") == "text" and part.text:
                content += part.text
        return {"content": content}

    @staticmethod
    def _extract_upstream_error(event: TaskArtifactUpdateEvent) -> Optional[dict]:
        """识别 VA 上游错误终态帧。

        AgentEngine ``versatile_proxy.py:336`` 把 ``event=='exception'`` 视为
        workflow_complete 终态。这里把 ``event in ("error", "exception")`` 都识别
        为终态错误，返回 data 部分（含 code/message）；非错误帧返回 None。

        识别后 Executor 应把 Task 标 FAILED 并清空 ``va_task_id``，避免下次请求被
        当成 cascade 续轮、用 stale task_id 调 VA 锁死 conversation。
        """
        for part in event.artifact.parts:
            if part.WhichOneof("content") != "data":
                continue
            frame = MessageToDict(part.data)
            if not isinstance(frame, dict):
                continue
            if frame.get("event") in ("error", "exception"):
                inner = frame.get("data")
                return inner if isinstance(inner, dict) else {}
        return None

    @staticmethod
    def _extract_failed_error(event: TaskStatusUpdateEvent) -> Optional[dict]:
        """从 VA Sidecar 的 FAILED 状态事件 message 中提取 upstream_error。"""
        if not event.status or not event.status.message:
            return None
        for part in event.status.message.parts:
            if part.WhichOneof("content") != "text" or not part.HasField("metadata"):
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
                inner = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
                return inner
            return {"message": raw_text}
        return None

    @staticmethod
    def _format_upstream_error(err: dict) -> str:
        """把上游 error/exception/upstream_error 拼成可展示的错误描述字符串。"""
        code = err.get("code")
        message = err.get("message") or err.get("msg") or ""
        if code:
            return f"执行报错，错误码：{code}，错误信息：{message}"
        return message or "VA 上游报错"

    @staticmethod
    def _build_failed_status_event(
        task_id: str, conv_id: str, error_text: str
    ) -> TaskStatusUpdateEvent:
        """构造带错误描述的 ``TaskStatusUpdateEvent(FAILED)``。

        ``status.message.parts[0].text`` 由 api.dispatch._extract_event_meta 转为
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
        turn_ctx: Any,
        upstream_error: dict,
    ) -> None:
        """VA 上游报错统一收尾：task FAILED + 清 va_task_id + enqueue FAILED 事件。

        让下次同 conv_id 请求重新走首轮（不再走续轮路径用 stale va_task_id 调 VA），
        破解原 INPUT_REQUIRED 路径下的 conversation 锁死。
        """
        task_id = turn_ctx.task_id
        conv_id = turn_ctx.conv_id
        error_text = self._format_upstream_error(upstream_error)

        await self._state_manager.finalize_failed(
            task_id, turn_ctx.call_context, error_text=error_text,
        )

        await turn_ctx.event_queue.enqueue_event(
            self._build_failed_status_event(task_id, conv_id, error_text),
        )
        logger.warning(
            f"[RemoteAgentHandler] VA 上游报错，task FAILED：conv={conv_id}, "
            f"code={upstream_error.get('code')}, "
            f"msg={(upstream_error.get('message') or '')!r:.80}"
        )

    @staticmethod
    def _extract_workflow_result(event: TaskStatusUpdateEvent) -> Optional[str]:
        """从 VA 的 COMPLETED 状态事件 message 中提取 workflow_result。

        VA 侧在 updater.complete(message) 时通过 Part 的 metadata {"vatype": "workflow_result"} 标识，
        DataPart 为纯文本 string_value。
        """
        if not event.status:
            return None
        if event.HasField("metadata"):
            meta = MessageToDict(event.metadata)
            cascade_result = meta.get("cascade_result")
            if cascade_result is not None:
                if isinstance(cascade_result, str):
                    return cascade_result
                return json.dumps(cascade_result, ensure_ascii=False)
        if not event.status.message:
            return None
        for part in event.status.message.parts:
            if not part.HasField("text") or not part.HasField("metadata"):
                continue
            meta = MessageToDict(part.metadata)
            if meta.get("vatype") == "workflow_result":
                return part.text
        return None

    async def _forward_artifact(
        self, turn_ctx: _TurnContext, event: TaskArtifactUpdateEvent, event_queue: EventQueue,
    ) -> None:
        """转发 VA artifact 事件：将 text Part 转换为 data Part 后入队。"""
        if event.artifact.HasField("metadata"):
            meta = MessageToDict(event.artifact.metadata)
            if meta.get("type") == "versatile_proxy":
                forwarded = TaskArtifactUpdateEvent()
                forwarded.CopyFrom(event)
                forwarded.task_id = turn_ctx.task_id
                forwarded.context_id = turn_ctx.conv_id
                await event_queue.enqueue_event(forwarded)
                return

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
        turn_ctx: Any,
        delegate: Any,
        context: Optional[Dict[str, Any]] = None,
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
        delegate_intent = _field(delegate, "intent")
        delegate_task_description = _field(delegate, "task_description")
        delegate_target_agent = _field(delegate, "target_agent")

        effective_intent, effective_query = _rewrite_recommend_delegate(
            delegate_intent,
            delegate_task_description,
        )
        if effective_intent != delegate_intent or effective_query != delegate_task_description:
            logger.info(
                "[RemoteAgentHandler] 推荐入口临时改写：intent={} -> {}, query={!r} -> {!r}",
                delegate_intent,
                effective_intent,
                delegate_task_description,
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
        agent_id = delegate_target_agent or ""

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
            f"[RemoteAgentHandler] [VersatileProxy] 开始调用 VA: conv={conv_id}, "
            f"intent={delegate_intent}, task_desc={delegate_task_description!r:.60}"
        )

        to_logger(
            message=build_versatile_start_observation(
                call_id=versatile_call_id,
                name=versatile_name,
                request_headers=headers,
                request_body=body,
            ),
            extra=Extra(tag=Tag.TAG_VERSATILE_START, cost=0),
        )

        all_chunks = []
        heartbeat_runtime = (context or {}).get("heartbeat_runtime")
        try:
            async for stream_resp in self._va_client.send_message(request):
                stream_resp_count += 1
                event = self._parse_stream_event(stream_resp)
                if event is None:
                    logger.debug(
                        f"[RemoteAgentHandler] [VersatileProxy] chunk #{stream_resp_count} "
                        f"解析为 None，跳过"
                    )
                    continue

                _log_va_chunk_debug(stream_resp_count, event)
                chunk_info = {
                    "index": stream_resp_count,
                    "event_type": type(event).__name__,
                    "content": _safe_dump_event(event),
                }
                all_chunks.append(chunk_info)

                if va_real_task_id is None and hasattr(event, "task_id") and event.task_id:
                    va_real_task_id = event.task_id
                    logger.debug(
                        f"[RemoteAgentHandler] VA real task_id={va_real_task_id}, conv={conv_id}"
                    )

                if isinstance(event, TaskArtifactUpdateEvent):
                    # data_proxy → 转换 text Part 后转发前端
                    await self._forward_artifact(turn_ctx, event, event_queue)
                    forwarded_count += 1
                    # 检测 error/exception 终态事件
                    err = self._extract_upstream_error(event)
                    if err is not None and upstream_error is None:
                        upstream_error = err

                elif isinstance(event, TaskStatusUpdateEvent):
                    if event.status.state == TASK_STATE_COMPLETED:
                        has_end_node = True
                        # 从 COMPLETED 状态事件的 message 中提取 workflow_result
                        wr = self._extract_workflow_result(event)
                        if wr is not None:
                            qa_result = wr
                            logger.debug(
                                f"[RemoteAgentHandler] [VersatileProxy] chunk #{stream_resp_count} "
                                f"status COMPLETED, workflow_result={wr!r:.60}"
                            )
                        logger.debug("[RemoteAgentHandler] VA TaskStatusUpdateEvent(COMPLETED)")
                    elif event.status.state == TASK_STATE_FAILED:
                        if upstream_error is None:
                            upstream_error = self._extract_failed_error(event) or {"message": "VA 任务异常终止"}
                        logger.debug("[RemoteAgentHandler] VA TaskStatusUpdateEvent(FAILED)")

        except asyncio.TimeoutError as e:
            if heartbeat_runtime is not None:
                await heartbeat_runtime.notify_heartbeat(
                    request_id=conv_id,
                    heartbeat_type="end",
                    status="timeout",
                    source="a2a_service",
                )
                await heartbeat_runtime.stop_heartbeat(conv_id, reason="timeout", mark_end=False)
            status_message = 1
            error_message = str(e)
            logger.exception(f"[RemoteAgentHandler] VA send_message 超时：{e}")
        except Exception as e:
            if heartbeat_runtime is not None:
                await heartbeat_runtime.notify_heartbeat(
                    request_id=conv_id,
                    heartbeat_type="end",
                    status="error",
                    source="a2a_service",
                )
                await heartbeat_runtime.stop_heartbeat(conv_id, reason="error", mark_end=False)
            status_message = 1
            error_message = str(e)
            logger.exception(f"[RemoteAgentHandler] VA send_message 异常：{e}")
        finally:
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
            # 在当前 VA span（由 _handle_delegate 的 with 创建）上补 response 属性
            _set_current_span_attrs({
                "openjiuwen.va.elapsed_ms": duration_ms,
                "openjiuwen.va.status": "failed" if (upstream_error is not None or error_message) else ("completed" if has_end_node else "suspended"),
                "openjiuwen.va.response_summary": json.dumps({"stream_resp_count": stream_resp_count, "has_end_node": has_end_node, "va_task_id": continuation_task_id}, ensure_ascii=False),
            })
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
                f"[RemoteAgentHandler] VA end node: conv={conv_id}, qa_result={qa_result!r:.60}"
            )
            return cascade, continuation_task_id, False

        if upstream_error is not None:
            await self._finalize_failed(turn_ctx, upstream_error)
            return None, "", True

        logger.info(
            f"[RemoteAgentHandler] VA 无 end node: conv={conv_id}, va_task={continuation_task_id}"
        )
        return None, continuation_task_id, False

    async def _continue_versatile_adapter(self, request: _ContinueVaRequest) -> None:
        """VA 挂起后，下一轮用户输入续轮。va_task_id 为 VA 真实 task_id。"""
        turn_ctx = request.turn_ctx
        va_task_id = request.va_task_id
        user_input = request.user_input
        headers = request.headers
        original_body = request.original_body
        context = request.context

        conv_id = turn_ctx.conv_id
        task_id = turn_ctx.task_id
        call_context = turn_ctx.call_context
        event_queue = turn_ctx.event_queue

        # params 仍从 Redis 首轮缓存取（保留 HEAD 的 params URL query 参数透传）
        cached_raw = await self._redis.get_json(session_request_key(conv_id))
        cached: dict[str, Any] = cached_raw if isinstance(cached_raw, dict) else {}
        params = cached.get("params", {})
        trace_id = cached.get("trace_id", "")
        agent_id = cached.get("agent_id", "")
        # 对齐 YGQ：续轮直接使用当前请求携带的 body，确保 buyStatus/tranNo 等
        # 当前轮输入能透传给下游工作流，而不是回退到首轮缓存 body。
        body = dict(original_body)
        body["stream"] = True
        raw_input_section = body.get("input")
        input_section: dict[str, Any] = raw_input_section if isinstance(raw_input_section, dict) else {}
        raw_custom_data = body.get("custom_data")
        custom_data: dict[str, Any] = raw_custom_data if isinstance(raw_custom_data, dict) else {}
        raw_custom_inputs = custom_data.get("inputs")
        custom_inputs: dict[str, Any] = raw_custom_inputs if isinstance(raw_custom_inputs, dict) else {}
        routed_intent = input_section.get("intent") or custom_inputs.get("intent") or ""

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
                target={"type": "workflow", "intent": routed_intent} if routed_intent else None,
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
        heartbeat_runtime = (context or {}).get("heartbeat_runtime")
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

                _log_va_chunk_debug(stream_resp_count, event)

                if isinstance(event, TaskArtifactUpdateEvent):
                    # data_proxy → 转换 text Part 后转发前端
                    await self._forward_artifact(turn_ctx, event, event_queue)
                    # 检测 error/exception 终态事件
                    err = self._extract_upstream_error(event)
                    if err is not None and upstream_error is None:
                        upstream_error = err

                elif isinstance(event, TaskStatusUpdateEvent):
                    if event.status.state == TASK_STATE_COMPLETED:
                        has_end_node = True
                        # 从 COMPLETED 状态事件的 message 中提取 workflow_result
                        wr = self._extract_workflow_result(event)
                        if wr is not None:
                            qa_result = wr
                        logger.debug("[RemoteAgentHandler] VA 续轮 TaskStatusUpdateEvent(COMPLETED)")
                    elif event.status.state == TASK_STATE_FAILED:
                        if upstream_error is None:
                            upstream_error = self._extract_failed_error(event) or {"message": "VA 任务异常终止"}
                        logger.debug("[RemoteAgentHandler] VA 续轮 TaskStatusUpdateEvent(FAILED)")

        except asyncio.TimeoutError as e:
            if heartbeat_runtime is not None:
                await heartbeat_runtime.notify_heartbeat(
                    request_id=conv_id,
                    heartbeat_type="end",
                    status="timeout",
                    source="a2a_service",
                )
                await heartbeat_runtime.stop_heartbeat(conv_id, reason="timeout", mark_end=False)
            status_message = 1
            error_message = str(e)
            logger.exception(f"[RemoteAgentHandler] VA continue send_message 超时：{e}")
        except Exception as e:
            if heartbeat_runtime is not None:
                await heartbeat_runtime.notify_heartbeat(
                    request_id=conv_id,
                    heartbeat_type="end",
                    status="error",
                    source="a2a_service",
                )
                await heartbeat_runtime.stop_heartbeat(conv_id, reason="error", mark_end=False)
            status_message = 1
            error_message = str(e)
            logger.exception(f"[RemoteAgentHandler] VA continue send_message 异常：{e}")
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
            # 在当前 VA span（由 _handle_request_resume 的 with 创建）上补 response 属性
            _set_current_span_attrs({
                "openjiuwen.va.elapsed_ms": duration_ms,
                "openjiuwen.va.status": "failed" if (upstream_error is not None or error_message) else ("completed" if has_end_node else "suspended"),
                "openjiuwen.va.response_summary": json.dumps({"stream_resp_count": stream_resp_count, "has_end_node": has_end_node, "va_task_id": va_task_id}, ensure_ascii=False),
            })
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
                f"[RemoteAgentHandler] VA 续轮 end node: conv={conv_id}, qa_result={qa_result!r:.60}"
            )
            await self._state_manager.update_task_status(
                task_id, "WORKING", call_context,
            )

            executor = (context or {}).get("executor")
            if executor is not None:
                await executor.run_agent(
                    turn_ctx,
                    query="",
                    original_body=original_body,
                    cascade_result=cascade,
                    run_options={
                        "heartbeat_runtime": (context or {}).get("heartbeat_runtime"),
                    },
                )

        elif upstream_error is not None:
            # VA 续轮也报错：同样落 FAILED + 清空 va_task_id，破解 conv_id 锁死
            await self._finalize_failed(turn_ctx, upstream_error)
        else:
            await self._state_manager.save_input_required(
                InputRequiredState(
                    task_id=task_id,
                    call_context=call_context,
                    remote_task_id=va_task_id or "",
                    workflow_id=None,
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=conv_id,
                    status=TaskStatus(state=TASK_STATE_INPUT_REQUIRED),
                )
            )
            logger.info(
                f"[RemoteAgentHandler] VA 续轮仍无 end node: conv={conv_id}, va_task={va_task_id}"
            )
