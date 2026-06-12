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

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional

import httpx
from a2a.client import Client, ClientFactory
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Artifact,
    Part,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    ROLE_AGENT,
    ROLE_USER,
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_WORKING,
)
from a2a.utils.errors import TaskNotFoundError, UnsupportedOperationError
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct, Value
from loguru import logger

from agents.EDPAgent import agent_stream
from common.constants import session_request_key
from common.events import (
    AnswerEvent,
    DelegateRequest,
    FinalAnswerChunkEvent,
    FinalAnswerEndEvent,
    MultiDelegateRequest,
    PlanningExecutionProcessEvent,
    SubAgentDispatchRequest,
    SubAgentResult,
    SubAgentSpec,
    SubTaskEvent,
    ToolStartEvent,
    WorkflowSpec,
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
from orchestrator.agent_adapter import agent_event_to_a2a, _completed
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


def _is_completed_status(status_update: TaskStatusUpdateEvent) -> bool:
    """判断 status_update 是否为 COMPLETED 终态。"""
    return bool(
        status_update
        and status_update.status
        and status_update.status.state == TASK_STATE_COMPLETED
    )


def extract_content(status_update: TaskStatusUpdateEvent) -> str:
    """从子 Agent 终态 status_update 提取最终回答内容（oneof 守卫）。

    ``Part`` 是 oneof（text/raw/url/data），**不可裸取 ``parts[0].text``**——若终态走
    ``DataPart``，``.text`` 为空串。按 ``part.WhichOneof("content")`` 判别：
      - text → ``part.text``（正常路径：FinalAnswerEndEvent.content 写入 TextPart）
      - data → 序列化 DataPart（对"终态走结构化结果"的防御）
    对齐 TECH §3.2 实现注 2。
    """
    if not (status_update and status_update.status and status_update.status.message):
        return ""
    for part in status_update.status.message.parts:
        which = part.WhichOneof("content")
        if which == "text":
            return part.text
        if which == "data":
            return json.dumps(MessageToDict(part.data), ensure_ascii=False)
    return ""


@dataclass(frozen=True)
class _TurnContext:
    """单轮 Executor 编排所需的上下文。

    将相关性较强的会话/任务/调用句柄打包传递，避免在内部方法间传入
    个数较多的散参数（参考 G.FNM.03）。

    ``sub_task_path`` 为本节点的绝对路径（root=()，主→子时子节点为 (entity_id,)…），
    来自入站 ``session_context``，用于级联盖章路径构造与递归深度门控。
    """

    conv_id: str
    task_id: str
    call_context: ServerCallContext
    event_queue: EventQueue
    sub_task_path: tuple[str, ...] = ()


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


class Executor(AgentExecutor):
    def __init__(
        self,
        va_client: Client,
        redis: RedisClient,
        task_store: RedisTaskStore,
        sub_agent_client: Optional[Client] = None,
        client_factory: Optional[ClientFactory] = None,
        max_concurrent_sub_agents: int = 3,
        sub_agent_timeout_seconds: int = 1800,
        max_parallel_workflows_per_agent: int = 3,
        workflow_timeout_seconds: int = 900,
        max_call_depth: int = 3,
    ) -> None:
        self._va_client = va_client
        self._redis = redis
        self._task_store = task_store
        # 子 Agent 寻址（P-006：url 由 Agent 自管、随派发请求下传，不再来自框架配置）：
        #   - client_factory：生产路径，按 spec.url 懒 create_from_url + 缓存（每 url 一个 client）。
        #   - sub_agent_client：单测/单部署兜底注入（存在时对所有 url 复用、忽略 url 差异）。
        # 两者皆无 → 无法派发，整批友好降级 failed；某 spec 缺 url/不可达 → 仅该实体降级。
        self._sub_agent_client = sub_agent_client
        self._client_factory = client_factory
        self._sub_agent_clients: dict[str, Client] = {}
        self.max_concurrent_sub_agents = max_concurrent_sub_agents
        self.sub_agent_timeout_seconds = sub_agent_timeout_seconds
        self.max_parallel_workflows_per_agent = max_parallel_workflows_per_agent
        self.workflow_timeout_seconds = workflow_timeout_seconds
        self.max_call_depth = max_call_depth
        # 取消令牌：conv_id → Event；并行调度期间注册，cancel/cancel_task 据此优雅退出。
        self._cancel_tokens: dict[str, asyncio.Event] = {}
        # 子任务记录：conv_id → {entity_id: {"task_id", "url"}}；供 cancel_task 深度传播
        # 与 P-007 崩溃恢复（url 动态，须随 task_id 一并持有/持久化）。
        self._child_task_ids: dict[str, dict[str, dict[str, str]]] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 子 Agent 经 DefaultRequestHandler 进入时绕过 user_router，
        # 在此从 data_part 的 session_context 补写 Redis 上下文（主 Agent 路径无 session_context、跳过）。
        await self._init_session_context_if_needed(context)

        conv_id = context.context_id or ""
        task_id = context.task_id or str(uuid.uuid4())
        call_context = context.call_context
        current_task = context.current_task

        # 从 message parts 提取 text、body、session_context（含 sub_task_path）
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

        turn_ctx = _TurnContext(
            conv_id=conv_id,
            task_id=task_id,
            call_context=call_context,
            event_queue=event_queue,
            sub_task_path=sub_task_path,
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
            # 立即入队 Task 快照：使父 Executor（_drive_sub_agent）在 LLM 首 token 前
            # 即可从首帧捕获 child_task_id（断线重连/协议级取消/崩溃恢复的前置）。
            # 主 Agent 路径：user_router._extract_event_meta 对 Task 类型返回 None，前端无感知。
            await event_queue.enqueue_event(new_task)
            logger.debug(f"[Executor] 创建 Task：task={task_id}, conv={conv_id}")

        await self.run_agent(
            turn_ctx,
            query=user_query,
            original_body=original_body,
            cascade_result=None,
            step_counter=[0],  # cascade 递归共享同一计数器
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """A2A CancelTask 入口（子 Agent 侧）：DefaultRequestHandler.on_cancel_task 调用。

        ① 若本会话正在并行调度（conv_id 在 _cancel_tokens 中），设置取消令牌
           → _handle_multi_delegate / _run_one_workflow 下一迭代检测并优雅退出。
        ② 向 event_queue 写入 CANCELED 终态事件 → on_cancel_task 的 consume_all
           读到终态后正常返回（否则永久阻塞）。
        随后 on_cancel_task 会 producer_task.cancel() 注入 CancelledError，execute()
        直接 re-raise 即可（cancel() 已发出终态事件）。
        """
        conv_id = context.context_id or ""
        token = self._cancel_tokens.get(conv_id)
        if token is not None:
            token.set()
        try:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id or "",
                    context_id=conv_id,
                    status=TaskStatus(state=TASK_STATE_CANCELED),
                )
            )
        except Exception as exc:  # noqa: BLE001 - best-effort，终态入队失败不阻断取消
            logger.warning(f"[Executor] cancel 入队 CANCELED 失败（忽略）：{exc!r}")
        logger.info(f"[Executor] cancel：conv={conv_id}")

    def _can_dispatch(self) -> bool:
        """是否具备派发能力：注入了单 client（兜底）或有 client_factory（按 url 构造）。"""
        return self._sub_agent_client is not None or self._client_factory is not None

    async def _get_sub_agent_client(self, url: str) -> Optional[Client]:
        """解析某 url 对应的子 Agent A2A client。

        - 注入了 ``sub_agent_client``（单测/单部署兜底）→ 直接复用，忽略 url 差异；
        - 否则用 ``client_factory`` 按 url 懒 ``create_from_url`` 并缓存（每 url 一个）；
        - url 为空或无 factory → 返回 None（由调用方将该实体降级 failed）。

        ``create_from_url`` 可能抛（子 Agent 不可达 / 卡片拉取失败）；不在此吞掉，
        由 ``_run_sub_agent`` 的 try/except 捕获 → 仅该实体 failed，不连坐其他。
        """
        if self._sub_agent_client is not None:
            return self._sub_agent_client
        if not url or self._client_factory is None:
            return None
        client = self._sub_agent_clients.get(url)
        if client is None:
            client = await self._client_factory.create_from_url(url)
            self._sub_agent_clients[url] = client
            logger.info(f"[Executor] sub_agent client 懒构造并缓存：url={url}")
        return client

    async def cancel_task(self, conv_id: str) -> None:
        """整会话取消（主 Agent 侧，由 user_router /cancel 端点调用，API 级深度取消）。

        1. 设置内存取消令牌（_drive_sub_agent / _run_one_workflow 迭代间隙检查）。
        2. 若具备派发能力，对本会话各子任务按其 url 发 A2A CancelTask 深度传播
           （best-effort，单个失败不阻断其他）。无派发能力时仅设令牌。
        """
        token = self._cancel_tokens.get(conv_id)
        if token is None:
            token = asyncio.Event()
            self._cancel_tokens[conv_id] = token
        token.set()
        logger.info(f"[Executor] cancel_task：conv={conv_id}，已设置取消令牌")

        if not self._can_dispatch():
            return
        for entity_id, info in self._child_task_ids.get(conv_id, {}).items():
            child_task_id = info.get("task_id", "")
            if not child_task_id:
                continue
            try:
                client = await self._get_sub_agent_client(info.get("url", ""))
                if client is None:
                    continue
                await client.cancel_task(CancelTaskRequest(id=child_task_id))
                logger.info(
                    f"[Executor] 已向子任务发 CancelTask：conv={conv_id}, "
                    f"entity={entity_id}, child={child_task_id}"
                )
            except Exception as exc:  # noqa: BLE001 - 深度取消 best-effort
                logger.warning(
                    f"[Executor] 子任务 CancelTask 失败（忽略）：child={child_task_id}, {exc!r}"
                )

    # ── 核心递归编排 ──────────────────────────────────────────────────────────

    async def run_agent(
        self,
        turn_ctx: _TurnContext,
        query: str,
        original_body: dict,
        cascade_result: Optional[dict],
        step_counter: Optional[list[int]] = None,
        reference_task_ids: Optional[list[str]] = None,
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
        # 捕获本轮最后一段 final_answer 文本：agent_adapter 不再把 final_answer_end
        # 映射成 COMPLETED，终态由本方法在 agent_stream 自然结束时统一发一次，
        # 其 content 取本轮最后出现的 final_answer 全量文本（即真正的最终回答）。
        # 注意：权威全量文本走 FinalAnswerChunkEvent（events.py "一次性全量通道"），
        # FinalAnswerEndEvent.content 仅是结束标记、默认为空串——若只读 end，子 Agent
        # 的 cascade content 恒为空，父 Agent 拿到 sub_agent_results[*].content="" 即编造
        # （见 ref_deploy_a2a_troubleshooting #7）。故以 chunk 为主、end/AnswerEvent 兜底。
        final_answer_content = ""
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
            if isinstance(event, FinalAnswerChunkEvent):
                final_answer_content = event.content or final_answer_content
            elif isinstance(event, FinalAnswerEndEvent):
                final_answer_content = event.content or final_answer_content
            elif isinstance(event, AnswerEvent) and getattr(event, "final", False):
                final_answer_content = event.content or final_answer_content
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

            # ── 并行子 Agent 派发（主 Agent 侧触发）────────────────────────
            if isinstance(event, SubAgentDispatchRequest):
                step_counter[0] += 1
                results, skipped_entities = await self._handle_sub_agent_dispatch(
                    event, turn_ctx, original_body
                )
                cascade = {
                    "sub_agent_results": [
                        {
                            "entity_id": r.entity_id,
                            "status": r.status,
                            "content": r.content,
                            "error": r.error,
                        }
                        for r in results
                    ],
                    # 被截断/超深实体经此告知（方案 A）：由汇总轮 LLM 生成提示；不算失败、不产 node 帧
                    "skipped_entities": skipped_entities,  # [{entity_id, entity_name, reason}]
                    "concurrency_limit": self.max_concurrent_sub_agents,
                }
                # cascade 续轮附 reference_task_ids，声明父子任务关联（外部经此关联各子任务）
                child_task_ids = [r.child_task_id for r in results if r.child_task_id]
                await self.run_agent(
                    turn_ctx,
                    query=query,
                    original_body=original_body,
                    cascade_result=cascade,
                    step_counter=step_counter,
                    reference_task_ids=child_task_ids,
                )
                return

            # ── 并行工作流派发（子 Agent 侧触发）──────────────────────────
            if isinstance(event, MultiDelegateRequest):
                step_counter[0] += 1
                # 注册取消令牌，使 cancel()（on_cancel_task）能找到并 set，令工作流优雅退出
                cancel_event = self._cancel_tokens.get(conv_id)
                if cancel_event is None:
                    cancel_event = asyncio.Event()
                    self._cancel_tokens[conv_id] = cancel_event
                try:
                    cascade = await self._handle_multi_delegate(event, turn_ctx, cancel_event)
                finally:
                    self._cancel_tokens.pop(conv_id, None)
                await self.run_agent(
                    turn_ctx,
                    query=query,
                    original_body=original_body,
                    cascade_result=cascade,
                    step_counter=step_counter,
                )
                return

            # 子 Agent 路径（sub_task_path 非空）须把中途 final_answer_end 降级为非终态
            # artifact：否则其 A2A server 在首段开场白 final_answer_end 处即判终态关流，
            # 后续 cascade（工作流）结果无法回传父侧。顶层主 Agent（sub_task_path 为空）
            # 保持原行为：final_answer_end → COMPLETED，前端报文序列不变。
            a2a_event = agent_event_to_a2a(
                event, task_id, conv_id,
                final_answer_terminal=not bool(turn_ctx.sub_task_path),
            )
            if a2a_event:
                await event_queue.enqueue_event(a2a_event)

        # agent stream 自然结束（非 DelegateRequest / 派发 cascade 续轮路径）→ 真正终态。
        # 写 COMPLETED 到 TaskStore（供 tasks/get、下一轮 task_id 重置）。
        task = await self._task_store.get(task_id, call_context)
        if task and task.status.state != TASK_STATE_COMPLETED:
            if reference_task_ids:
                # 声明父子任务关联（A2A 协议级，供外部经 tasks/get 关联各子任务）
                task.metadata.update({"reference_task_ids": reference_task_ids})
            task.status.CopyFrom(TaskStatus(state=TASK_STATE_COMPLETED))
            await self._task_store.save(task, call_context)
            logger.debug(f"[Executor] Task 标记 COMPLETED：task={task_id}, conv={conv_id}")
        # 子 Agent 路径：中途 final_answer_end 已降级为 artifact、未发任何 COMPLETED，
        # 故在此补发唯一一次终态 COMPLETED——父侧 _drive_sub_agent 据此 return，并取
        # final_answer 作为子 Agent 结果。顶层主 Agent 的终态由其末尾 final_answer_end →
        # COMPLETED 承担（user_router 渲染 final_answer_end + 抽干至 close），此处不重复发。
        if turn_ctx.sub_task_path:
            await event_queue.enqueue_event(
                _completed(task_id, conv_id, final_answer_content)
            )

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

    def _extract_upstream_error(self, event: TaskArtifactUpdateEvent) -> Optional[dict]:
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

    @staticmethod
    def _extract_data_proxy_frames(event: TaskArtifactUpdateEvent) -> list[dict]:
        """从 VA artifact 取出 ``vatype=data_proxy`` 的 text Part，json.loads 还原为结构化帧列表。

        新 VA（sidecar）把业务帧用**文本**装在 ``data_proxy`` text Part 里下传，这层
        text→struct 的「解包」逻辑特意留在 a2a 侧（仍需使用）。直调路径 ``_forward_artifact`` 与
        并行路径 ``_drive_workflow_va`` 共用本 helper，保证两路转发的 inner 逐字段一致——
        并行帧 = 直调帧 + 仅外层一层 ``sub_task`` 信封（P-009）。
        """
        frames: list[dict] = []
        for part in event.artifact.parts:
            if not part.HasField("text") or not part.HasField("metadata"):
                continue
            meta = MessageToDict(part.metadata)
            if meta.get("vatype") != "data_proxy":
                continue
            try:
                frames.append(json.loads(part.text))
            except ValueError:
                logger.error("[Executor] [VersatileProxy] 待转发内容不是结构化信息，跳过不处理")
        return frames

    async def _forward_artifact(
        self, turn_ctx: _TurnContext, event: TaskArtifactUpdateEvent, event_queue: EventQueue,
    ) -> None:
        """转发 VA artifact 事件：将 text Part 转换为 data Part 后入队。"""
        parts = [
            Part(data=ParseDict(frame, Value()))
            for frame in self._extract_data_proxy_frames(event)
        ]

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
                    # data_proxy → 转换 text Part 后转发前端（_forward_artifact 以 turn_ctx 的
                    # task_id/conv_id 新建事件，已隐含 owner 重盖，吸收原 C-002 _restamp_to_owner）
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
                            upstream_error = {"message": "VA 任务异常终止"}
                        logger.debug("[Executor] VA TaskStatusUpdateEvent(FAILED)")

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
                task_id="",
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
                    # data_proxy → 转换 text Part 后转发前端（owner 重盖见 _forward_artifact）
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
                            upstream_error = {"message": "VA 任务异常终止"}
                        logger.debug("[Executor] VA 续轮 TaskStatusUpdateEvent(FAILED)")

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

    # ── 子会话上下文初始化（子 Agent 侧）─────────────────────────────────────

    def _extract_session_context(self, context: RequestContext) -> dict:
        """从 A2A message 的 data_part 提取 session_context（主 Executor 透传而来）。"""
        if not context.message:
            return {}
        for part in context.message.parts:
            if part.WhichOneof("content") == "data":
                data = MessageToDict(part.data)
                if isinstance(data, dict):
                    sc = data.get("session_context")
                    if isinstance(sc, dict):
                        return sc
        return {}

    async def _init_session_context_if_needed(self, context: RequestContext) -> None:
        """子 Agent 经 DefaultRequestHandler 进入时，以 set_nx 语义补写 Redis 上下文。

        主 Agent 路径（user_router 已写 Redis、无 session_context）直接跳过，零影响。
        """
        session_ctx = self._extract_session_context(context)
        if not session_ctx:
            return
        conv_id = context.context_id or ""
        if not conv_id:
            return
        existing = await self._redis.get_json(session_request_key(conv_id))
        if existing is not None:
            return
        await self._redis.set_json(
            session_request_key(conv_id),
            {
                "headers": session_ctx.get("headers", {}),   # VA 鉴权核心，继承父请求
                "trace_id": session_ctx.get("trace_id", ""),  # 链路可观测性
                "agent_id": getattr(get_settings(), "dpa_agent_id", "") or "",  # 子 Agent 自身 ID
                "params": session_ctx.get("params", {}),   # 继承父请求 URL query（如 type/workspace_id），供子 Agent 工作流调用 VA 时透传
                "body": {},     # 子 Agent 自行构造 VA 请求体，初始为空
            },
            ex=_TTL,
        )
        logger.info(f"[Executor] 子会话上下文已写 Redis：conv={conv_id}")

    # ── 级联盖章工具 ─────────────────────────────────────────────────────────

    async def _emit_sub_task(
        self,
        turn_ctx: _TurnContext,
        sub_task_path,
        node_kind: str,
        data: dict,
    ) -> None:
        """构造 SubTaskEvent 并经 agent_adapter 序列化入队（盖章 / 透传统一出口）。"""
        ev = SubTaskEvent(
            sub_task_path=[str(p) for p in sub_task_path],
            node_kind=node_kind,
            data=data,
        )
        a2a = agent_event_to_a2a(ev, turn_ctx.task_id, turn_ctx.conv_id)
        if a2a is not None:
            await turn_ctx.event_queue.enqueue_event(a2a)

    # ── 主 Agent 侧：并行子 Agent 调度 ───────────────────────────────────────

    async def _handle_sub_agent_dispatch(
        self,
        dispatch: SubAgentDispatchRequest,
        turn_ctx: _TurnContext,
        original_body: dict,
    ) -> tuple[list[SubAgentResult], list[dict]]:
        """并行子 Agent 派发：递归深度门控① + 并发截断② + gather。

        返回 ``(results, skipped_entities)``；skipped_entities 经 cascade 告知汇总轮 LLM
        （方案 A，不产 node 帧、不算失败）。子 Agent 自行构造 VA body，故 original_body 不下传。
        """
        conv_id = turn_ctx.conv_id
        specs = list(dispatch.specs)
        skipped: list[dict] = []

        # 闸门①·递归深度：self_depth = len(sub_task_path)（root=0，主→子=1…）
        self_depth = len(turn_ctx.sub_task_path)
        if self_depth + 1 > self.max_call_depth:
            for s in specs:
                skipped.append(
                    {"entity_id": s.entity_id, "entity_name": s.entity_name,
                     "reason": "max_call_depth"}
                )
            logger.warning(
                f"[Executor] 递归深度门控：depth={self_depth}+1 > {self.max_call_depth}，"
                f"{len(specs)} 实体全部跳过"
            )
            return [], skipped

        # 闸门②·并发截断（取前 N，按 Query 顺序）
        n = self.max_concurrent_sub_agents
        dispatched = specs[:n]
        for s in specs[n:]:
            skipped.append(
                {"entity_id": s.entity_id, "entity_name": s.entity_name,
                 "reason": "concurrency_limit"}
            )
        if not dispatched:
            return [], skipped

        # 无派发能力（既无注入 client 又无 client_factory）→ 整批友好降级为 failed。
        # 注：单个 spec 缺 url / 不可达只降级该实体（见 _drive_sub_agent），不到这里。
        if not self._can_dispatch():
            logger.warning("[Executor] 无子 Agent 派发能力（无 client/factory），无法并行派发")
            results = [
                SubAgentResult(entity_id=s.entity_id, status="failed", error="子Agent调度未启用")
                for s in dispatched
            ]
            return results, skipped

        cancel_event = self._cancel_tokens.get(conv_id)
        if cancel_event is None:
            cancel_event = asyncio.Event()
            self._cancel_tokens[conv_id] = cancel_event
        self._child_task_ids.setdefault(conv_id, {})

        try:
            gathered = await asyncio.gather(
                *[
                    self._run_sub_agent_with_timeout(s, turn_ctx, cancel_event)
                    for s in dispatched
                ],
                return_exceptions=True,
            )
        finally:
            self._cancel_tokens.pop(conv_id, None)
            self._child_task_ids.pop(conv_id, None)

        results: list[SubAgentResult] = []
        for s, r in zip(dispatched, gathered):
            if isinstance(r, SubAgentResult):
                results.append(r)
            else:
                logger.error(f"[Executor] 子 Agent 异常：entity={s.entity_id}, {r!r}")
                results.append(
                    SubAgentResult(entity_id=s.entity_id, status="failed", error=str(r))
                )
        return results, skipped

    async def _run_sub_agent_with_timeout(
        self,
        spec: SubAgentSpec,
        turn_ctx: _TurnContext,
        cancel_event: asyncio.Event,
    ) -> SubAgentResult:
        """asyncio.wait_for 包裹单个子 Agent；超时 → node_end(timeout) + SubAgentResult(timeout)。"""
        child_path = tuple(turn_ctx.sub_task_path) + (spec.entity_id,)
        try:
            return await asyncio.wait_for(
                self._run_sub_agent(spec, turn_ctx, cancel_event, child_path),
                timeout=self.sub_agent_timeout_seconds,
            )
        except asyncio.TimeoutError:
            await self._emit_sub_task(
                turn_ctx, child_path, "agent",
                {"event": "node_end", "status": "timeout", "error": "子Agent执行超时"},
            )
            logger.warning(f"[Executor] 子 Agent 超时：entity={spec.entity_id}")
            return SubAgentResult(
                entity_id=spec.entity_id, status="timeout", error="子Agent执行超时"
            )

    async def _run_sub_agent(
        self,
        spec: SubAgentSpec,
        turn_ctx: _TurnContext,
        cancel_event: asyncio.Event,
        child_path: tuple[str, ...],
    ) -> SubAgentResult:
        """驱动单个子 Agent：node_start → 盖章/透传 report → node_end，并捕获 child_task_id。"""
        conv_id = turn_ctx.conv_id
        sub_conv_id = f"{conv_id}:sub:{spec.entity_id}"
        await self._emit_sub_task(
            turn_ctx, child_path, "agent",
            {"event": "node_start", "entity_name": spec.entity_name},
        )

        content = ""
        child_task_id = ""
        try:
            async for frame in self._drive_sub_agent(
                spec, sub_conv_id, child_path, turn_ctx, cancel_event
            ):
                ftype = frame.get("type")
                if ftype == "__task_created__":
                    child_task_id = frame.get("task_id", "")
                    if child_task_id:
                        self._child_task_ids.setdefault(conv_id, {})[spec.entity_id] = {
                            "task_id": child_task_id, "url": spec.url,
                        }
                    continue
                if ftype == "__completed__":
                    content = frame.get("content", "")
                    continue
                # report 帧：已盖章（更深层 sub_task）透传 / 否则盖章为本节点 agent 帧
                if frame.get("type") == "sub_task":
                    await self._emit_sub_task(
                        turn_ctx,
                        frame.get("sub_task_path") or list(child_path),
                        frame.get("node_kind") or "agent",
                        frame.get("data") or {},
                    )
                else:
                    await self._emit_sub_task(turn_ctx, child_path, "agent", frame)
        except Exception as exc:  # noqa: BLE001 - 单子 Agent 故障隔离，不影响其他子
            await self._emit_sub_task(
                turn_ctx, child_path, "agent",
                {"event": "node_end", "status": "failed", "error": str(exc)},
            )
            logger.exception(f"[Executor] 子 Agent 失败：entity={spec.entity_id}, {exc!r}")
            return SubAgentResult(
                entity_id=spec.entity_id, status="failed",
                error=str(exc), child_task_id=child_task_id,
            )

        if cancel_event.is_set():
            await self._emit_sub_task(
                turn_ctx, child_path, "agent",
                {"event": "node_end", "status": "cancelled", "reason": "用户取消"},
            )
            return SubAgentResult(
                entity_id=spec.entity_id, status="cancelled", child_task_id=child_task_id
            )

        await self._emit_sub_task(
            turn_ctx, child_path, "agent",
            {"event": "node_end", "status": "done", "content": content},
        )
        return SubAgentResult(
            entity_id=spec.entity_id, status="done",
            content=content, child_task_id=child_task_id,
        )

    def _build_sub_agent_message(
        self, query: str, sub_conv_id: str, session_context: dict
    ) -> SendMessageRequest:
        """构造发往子 Agent 的 A2A 消息，session_context 附入 data_part 供子侧写 Redis。"""
        text_part = Part()
        text_part.text = query
        data_struct = Struct()
        data_struct.update({"session_context": session_context})
        data_value = Value()
        data_value.struct_value.CopyFrom(data_struct)
        data_part = Part()
        data_part.data.CopyFrom(data_value)
        msg = Message(
            role=ROLE_USER,
            message_id=str(uuid.uuid4()),
            task_id="",
            context_id=sub_conv_id,
        )
        msg.parts.extend([text_part, data_part])
        return SendMessageRequest(message=msg)

    @staticmethod
    def _parse_artifact_frame(artifact_update: TaskArtifactUpdateEvent) -> Optional[dict]:
        """从子 Agent 的 artifact_update 取出 data_part 原帧 dict。"""
        if not (artifact_update and artifact_update.artifact):
            return None
        for part in artifact_update.artifact.parts:
            if part.WhichOneof("content") == "data":
                frame = MessageToDict(part.data)
                if isinstance(frame, dict):
                    return frame
        return None

    async def _fetch_final_via_get(
        self, child_task_id: str, client: Optional[Client]
    ) -> Optional[dict]:
        """resubscribe 不可用时回退 tasks/get 取最终结果（仅 COMPLETED 才视为成功）。"""
        if not child_task_id or client is None:
            return None
        try:
            task = await client.get_task(GetTaskRequest(id=child_task_id))
        except Exception as exc:  # noqa: BLE001 - 取不到即返回 None，由上层转 failed
            logger.warning(f"[Executor] tasks/get 失败：child={child_task_id}, {exc!r}")
            return None
        if task and task.status and task.status.state == TASK_STATE_COMPLETED:
            return {"content": extract_content(task)}
        return None

    async def _drive_sub_agent(
        self,
        spec: SubAgentSpec,
        sub_conv_id: str,
        child_path: tuple[str, ...],
        turn_ctx: _TurnContext,
        cancel_event: asyncio.Event,
    ) -> AsyncGenerator[dict, None]:
        """驱动子 Agent A2A 流：首帧捕获 child_task_id、断线重连（终态→tasks/get）。

        每帧为 ``StreamResponse``（protobuf oneof），按 ``WhichOneof("payload")`` 判别——
        **不可** ``isinstance(stream_resp, Task/...)``（恒不命中）。对齐 TECH §3.2。

        client 按 ``spec.url`` 解析（P-006）：缺 url / 不可达 → 抛错，由 _run_sub_agent
        捕获 → 仅该实体降级 failed。
        """
        client = await self._get_sub_agent_client(spec.url)
        if client is None:
            raise RuntimeError(
                f"子 Agent 不可达或缺少 url：entity={spec.entity_id}, url={spec.url!r}"
            )
        parent_cached = await self._redis.get_json(
            session_request_key(turn_ctx.conv_id)
        ) or {}
        session_context = {
            "headers": parent_cached.get("headers", {}),   # VA 鉴权 token
            "params": parent_cached.get("params", {}),      # 前端 URL query（如 type/workspace_id）继承给子 Agent 的 VA 调用
            "trace_id": parent_cached.get("trace_id", ""),  # 链路追踪
            "sub_task_path": list(child_path),              # 下传绝对路径：子节点继续盖章 / 深度门控
        }
        request = self._build_sub_agent_message(spec.query, sub_conv_id, session_context)

        child_task_id: Optional[str] = None
        retry_count = 0
        max_retries = 3

        while True:
            try:
                if child_task_id is None:
                    stream = client.send_message(request)
                else:
                    stream = client.subscribe(
                        SubscribeToTaskRequest(id=child_task_id)
                    )
                async for stream_resp in stream:
                    retry_count = 0   # 成功收帧，重置退避计数
                    if cancel_event.is_set():
                        return
                    kind = stream_resp.WhichOneof("payload")
                    if kind == "task":
                        if child_task_id is None:
                            child_task_id = stream_resp.task.id
                            yield {"type": "__task_created__", "task_id": child_task_id}
                    elif kind == "artifact_update":
                        frame = self._parse_artifact_frame(stream_resp.artifact_update)
                        if frame is not None:
                            yield frame
                    elif kind == "status_update":
                        if _is_completed_status(stream_resp.status_update):
                            yield {
                                "type": "__completed__",
                                "content": extract_content(stream_resp.status_update),
                            }
                            return
                return  # 流正常结束

            except (UnsupportedOperationError, TaskNotFoundError):
                # 终态任务 / 队列已关 → resubscribe 不可用。这≠失败：子 Agent 可能断线期间已成功跑完，
                # 必须回退 tasks/get 取结果，拿不到才转 failed。
                final = await self._fetch_final_via_get(child_task_id or "", client)
                if final is not None:
                    yield {"type": "__completed__", **final}
                    return
                raise
            except (httpx.RemoteProtocolError, httpx.ReadError, OSError) as exc:
                if child_task_id is None:
                    raise   # 首帧前断线，无 task_id 可重连/查询 → failed（wait_for 兜底）
                if retry_count >= max_retries:
                    final = await self._fetch_final_via_get(child_task_id, client)
                    if final is not None:
                        yield {"type": "__completed__", **final}
                        return
                    raise
                retry_count += 1
                logger.warning(
                    f"[Executor] 子 Agent SSE 断连，重连 #{retry_count}："
                    f"child={child_task_id}, {exc!r}"
                )
                await asyncio.sleep(2 ** retry_count)   # 指数退避 2s / 4s / 8s

    # ── 子 Agent 侧：并行工作流调度 ──────────────────────────────────────────

    async def _handle_multi_delegate(
        self,
        req: MultiDelegateRequest,
        turn_ctx: _TurnContext,
        cancel_event: asyncio.Event,
    ) -> dict:
        """并行工作流派发：超限直接全拒绝（保护 VA 后端，不取前 N）；否则 gather。

        返回 ``{"workflow_results": [{workflow_id, status, result, error, elapsed_ms}, ...]}``。
        """
        workflows = list(req.workflows)
        if len(workflows) > self.max_parallel_workflows_per_agent:
            logger.warning(
                f"[Executor] 并行工作流超限：{len(workflows)} > "
                f"{self.max_parallel_workflows_per_agent}，全部拒绝"
            )
            err = f"并行工作流数超限（最大 {self.max_parallel_workflows_per_agent}）"
            return {
                "workflow_results": [
                    {"workflow_id": w.workflow_id, "status": "failed",
                     "result": None, "error": err, "elapsed_ms": 0}
                    for w in workflows
                ]
            }
        results = await asyncio.gather(
            *[self._run_one_workflow(w, turn_ctx, cancel_event) for w in workflows]
        )
        return {"workflow_results": list(results)}

    async def _run_one_workflow(
        self,
        spec: WorkflowSpec,
        turn_ctx: _TurnContext,
        cancel_event: asyncio.Event,
    ) -> dict:
        """驱动单个并行工作流：node_start → VA report 盖章 → node_end，记录 elapsed_ms。"""
        wf_path = tuple(turn_ctx.sub_task_path) + (spec.workflow_id,)
        await self._emit_sub_task(
            turn_ctx, wf_path, "workflow",
            {"event": "node_start", "intent": spec.intent},
        )
        started = time.monotonic()
        delegate = DelegateRequest(**spec.va_fields())  # 显式转换：workflow_id 不传给 VA

        try:
            final_result = await asyncio.wait_for(
                self._drive_workflow_va(delegate, turn_ctx, wf_path, cancel_event),
                timeout=self.workflow_timeout_seconds,
            )
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            await self._emit_sub_task(
                turn_ctx, wf_path, "workflow",
                {"event": "node_end", "status": "timeout", "error": "工作流超时"},
            )
            logger.warning(f"[Executor] 工作流超时：workflow={spec.workflow_id}")
            return {"workflow_id": spec.workflow_id, "status": "timeout",
                    "result": None, "error": "工作流超时", "elapsed_ms": elapsed_ms}
        except Exception as exc:  # noqa: BLE001 - 单工作流故障隔离
            elapsed_ms = int((time.monotonic() - started) * 1000)
            await self._emit_sub_task(
                turn_ctx, wf_path, "workflow",
                {"event": "node_end", "status": "failed", "error": str(exc)},
            )
            logger.exception(f"[Executor] 工作流失败：workflow={spec.workflow_id}, {exc!r}")
            return {"workflow_id": spec.workflow_id, "status": "failed",
                    "result": None, "error": str(exc), "elapsed_ms": elapsed_ms}

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if cancel_event.is_set():
            await self._emit_sub_task(
                turn_ctx, wf_path, "workflow",
                {"event": "node_end", "status": "cancelled", "reason": "用户取消"},
            )
            return {"workflow_id": spec.workflow_id, "status": "cancelled",
                    "result": final_result, "error": "", "elapsed_ms": elapsed_ms}

        node_end_result = dict(final_result) if isinstance(final_result, dict) else {"value": final_result}
        node_end_result["elapsed_ms"] = elapsed_ms
        await self._emit_sub_task(
            turn_ctx, wf_path, "workflow",
            {"event": "node_end", "status": "done", "result": node_end_result},
        )
        return {"workflow_id": spec.workflow_id, "status": "done",
                "result": final_result, "error": "", "elapsed_ms": elapsed_ms}

    async def _drive_workflow_va(
        self,
        delegate: DelegateRequest,
        turn_ctx: _TurnContext,
        wf_path: tuple[str, ...],
        cancel_event: asyncio.Event,
    ) -> Optional[dict]:
        """向 VA 发起单工作流调用，把每条 data_proxy 帧盖章为 workflow report；返回 workflow_result。

        新 VA（sidecar）契约：业务帧走 ``data_proxy`` text Part（经 ``_extract_data_proxy_frames``
        还原），完成走 ``TaskStatusUpdateEvent(COMPLETED)``（带 ``vatype=workflow_result``）。
        盖章的 inner = 直调路径同款原始 parsed（决策 B：仅外层多一层 sub_task 信封）。
        """
        conv_id = turn_ctx.conv_id
        cached = await self._redis.get_json(session_request_key(conv_id)) or {}
        headers = cached.get("headers", {})
        body = dict(cached.get("body", {}))
        params = cached.get("params", {})
        trace_id = cached.get("trace_id", "")

        # 用 delegate 的 query/intent 覆盖 body（与 _call_versatile_adapter 同构）
        input_section = dict(body.get("input") or {})
        input_section["query"] = delegate.task_description
        input_section["intent"] = delegate.intent
        body["input"] = input_section
        custom_data = dict(body.get("custom_data") or {})
        custom_inputs = dict(custom_data.get("inputs") or {})
        custom_inputs["query"] = delegate.task_description
        custom_inputs["intent"] = delegate.intent
        custom_data["inputs"] = custom_inputs
        body["custom_data"] = custom_data
        body["stream"] = True

        request = self._build_va_message(
            _VaRequestPayload(
                query=delegate.task_description,
                headers=headers,
                body=body,
                params=params,
                task_id="",
                conv_id=conv_id,
                trace_id=trace_id,
                agent_id=delegate.target_agent or "",
            )
        )

        final_result: Optional[dict] = None
        stream = self._va_client.send_message(request)
        async for stream_resp in stream:
            if cancel_event.is_set():
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()
                break
            event = self._parse_stream_event(stream_resp)
            if event is None:
                continue
            if isinstance(event, TaskArtifactUpdateEvent):
                # data_proxy 帧盖章为 workflow report；inner = 直调同款原始 parsed（决策 B）
                for frame in self._extract_data_proxy_frames(event):
                    await self._emit_sub_task(turn_ctx, wf_path, "workflow", frame)
            elif isinstance(event, TaskStatusUpdateEvent):
                state = event.status.state
                if state == TASK_STATE_COMPLETED:
                    # controller 适配器在 End + 约定的 workflow_result_node QA 命中时带 result
                    final_result = {"workflow_result": self._extract_workflow_result(event)}
                    break
                elif state == TASK_STATE_FAILED:
                    raise RuntimeError(f"VA 工作流异常终止：path={list(wf_path)}")
                elif state == TASK_STATE_INPUT_REQUIRED:
                    # 本需求声明不支持中断（PENDING P-011）；并行 fire-and-gather 无法续轮 →
                    # 防御性当异常失败，不静默（同事确认本场景不应出现该终态）。
                    raise RuntimeError(
                        f"VA 工作流返回 INPUT_REQUIRED，本场景不支持中断（P-011）：path={list(wf_path)}"
                    )
        return final_result
