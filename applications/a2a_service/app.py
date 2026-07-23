# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
A2A Service 进程入口。

暴露端点：
  POST /v1/{project_id}/agents/{agent_id}/conversations/{conv_id}  — 定制化 Versatile 入口
  GET  /a2a/.well-known/agent-card.json                            — A2A 标准 Agent Card
  POST /a2a/                                                        — A2A 标准 JSON-RPC 入口

两条路径共用同一个 Executor + RedisTaskStore，Task 状态一致。
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from fastapi import FastAPI
from loguru import logger
from starlette.applications import Starlette

from agents.EDPAgent import initialize
from common.redis_client import RedisClient
from common.redis_task_store import ReadOnlyTaskStore, RedisTaskStore
from config import get_settings
from channels.registry import AdapterRegistry
from api.dispatch import router as dispatch_router
from orchestrator.executor import Executor
from orchestrator.route import RouteDispatcher
from orchestrator.state import TaskStateManager
from tools.simulate_router.simulate import router as simulate_router
from common.data_store_factory import build_runtime_state_store_and_db_handler
from common.kv_adapter import KvAdapter
from common.task_store_adapter import TaskStoreAdapter


def _load_env_to_environ() -> None:
    # pydantic-settings 读 .env 只填充 Settings 对象，不写回 os.environ；
    # 而 httpx(trust_env=True) 与本模块的 NO_PROXY 合并逻辑都读 os.environ。
    # 这里用 load_dotenv 把 .env 同步进 os.environ，让代理相关变量对 httpx 生效。
    # override=False：容器 ENV / 真实环境变量优先级更高，.env 仅作兜底。
    _env_file = Path(__file__).parent / ".env"
    if not _env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)
    except ImportError:
        logger.warning("[NO_PROXY] python-dotenv 未安装，跳过 .env → os.environ 同步")


def _init_no_proxy() -> None:
    # 先把 .env 中的代理变量加载进 os.environ（pydantic-settings 不会做这一步）
    _load_env_to_environ()
    # 合并 NO_PROXY：保留本地地址兜底（localhost/127.0.0.1），同时并入 env 已有配置，
    # 使实际场景可通过 .env 的 NO_PROXY 增加需绕过代理的地址。
    # 大小写同步设置，避免部分库（requests/urllib3）只读小写 no_proxy 导致行为不一致。
    local_defaults = {"localhost", "127.0.0.1"}
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    merged = local_defaults | {h.strip() for h in existing.split(",") if h.strip()}
    value = ",".join(sorted(merged))
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    # 调试日志：确认运行阶段是否正确拿到 env 中的 NO_PROXY 配置
    logger.info(
        f"[NO_PROXY] env 原始值={existing!r} 合并后={value!r} "
        f"HTTP_PROXY={os.environ.get('HTTP_PROXY')!r} HTTPS_PROXY={os.environ.get('HTTPS_PROXY')!r}"
    )


_init_no_proxy()


LOG_FIELD_SEPARATOR = '\x01'


def dynamic_format(record):
    # 安全获取 extra 中的字段，如果不存在则使用默认值
    extra = record.get("extra", {})
    trace_id = extra.get("trace_id", "default_trace_id")
    agent_id = extra.get("agent_id", "default_agent_id")
    conversation_id = extra.get("conversation_id", "default_conversation_id")

    if len(extra) > 0 and "tag" not in extra:
        return (
            f"<green>{{time:YYYY-MM-DD HH:mm:ss.SSS}}</green>{LOG_FIELD_SEPARATOR}"
            f"<level>{{level.name:<8}}</level>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{{name}}</cyan>:<cyan>{{function}}</cyan>:<cyan>{{line}}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{trace_id}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{agent_id}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{conversation_id}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<level>{{message}}</level>\n"
        )
    elif "tag" in extra:  # tag类日志
        tag = extra.get("tag", "N/A")
        cost = extra.get("cost", "N/A")
        return (
            f"<green>{{time:YYYY-MM-DD HH:mm:ss.SSS}}</green>{LOG_FIELD_SEPARATOR}"
            f"<level>{{level.name:<8}}</level> \x01 "
            f"<cyan>{{name}}</cyan>:<cyan>{{function}}</cyan>:<cyan>{{line}}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{trace_id}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{agent_id}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{conversation_id}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{tag}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{cost}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<level>{{message}}</level>\n"
        )
    else:
        return (
            f"<green>{{time:YYYY-MM-DD HH:mm:ss.SSS}}</green>{LOG_FIELD_SEPARATOR}"
            f"<level>{{level.name:<8}}</level>{LOG_FIELD_SEPARATOR}"
            f"<cyan>{{name}}</cyan>:<cyan>{{function}}</cyan>:<cyan>{{line}}</cyan>{LOG_FIELD_SEPARATOR}"
            f"<level>{{message}}</level>\n"
        )


def _cleanup_logs(files):
    """
    loguru retention 回调函数：按天数 + 按总空间清理。

    loguru 在每次轮转后调用此函数，传入所有匹配的日志文件列表。
    注意：loguru 传入的可能是 Path 对象或字符串，统一转为 Path 处理。

    清理逻辑：
    1. 按保留天数清理：删除修改时间超过 retention_days 的归档文件
    2. 按总空间清理：如果总空间超限，从最旧归档文件开始逐一删除

    注意：活跃文件（无 .gz 后缀的 .log 文件）不参与删除，只删 .gz 归档文件。
    """
    _settings = get_settings()
    retention_days = _settings.log_retention_days
    max_total_size = _settings.log_max_total_size

    if not files:
        return

    # 统一转为 Path 对象（loguru 可能传入字符串）
    files = [Path(f) for f in files]

    # 按文件名区分活跃文件和归档文件（不用 mtime 猜测，避免误删刚压缩的归档）
    # 活跃文件：无 .gz 后缀的 .log 文件（正在被 loguru 写入）
    # 归档文件：.gz 后缀的压缩文件（已被轮转压缩）
    active_files = [f for f in files if f.exists() and not f.name.endswith(".gz")]
    archive_files = [f for f in files if f.exists() and f.name.endswith(".gz")]

    if not archive_files:
        return

    # 1. 按天数清理（只清理归档文件）
    if retention_days > 0:
        cutoff_time = (datetime.now(tz=timezone.utc) - timedelta(days=retention_days)).timestamp()
        for f in archive_files:
            try:
                if f.stat().st_mtime < cutoff_time:
                    f.unlink()
            except OSError as e:
                # 用 print 到 stderr，不用 loguru（避免在 retention 回调中递归调用 loguru）
                sys.stderr.write(f"[log_cleanup] WARN 按天数清理删除失败: {f} -> {e}\n")

    # 2. 按总空间清理（活跃文件 + 剩余归档文件的总和）
    if max_total_size > 0:
        # 重新获取未被天数清理删掉的归档文件
        remaining_archives = [(f.stat().st_mtime, f) for f in archive_files if f.exists()]
        active_size = sum(f.stat().st_size for f in active_files if f.exists())
        archive_total = sum(f.stat().st_size for _, f in remaining_archives)
        total_size = active_size + archive_total

        if total_size > max_total_size:
            remaining_archives.sort(key=lambda x: x[0])  # 最旧在前
            for _mtime, f in remaining_archives:
                if total_size <= max_total_size:
                    break
                try:
                    file_size = f.stat().st_size
                    f.unlink()
                    total_size -= file_size
                except OSError as e:
                    sys.stderr.write(f"[log_cleanup] WARN 按空间清理删除失败: {f} -> {e}\n")


def setup_logging() -> None:
    settings = get_settings()

    logger.remove()

    logger.add(
        sys.stderr,
        level=settings.log_level.upper() if settings.log_level else "INFO",
        format=dynamic_format,
        filter=lambda record: len(record["extra"]) == 0 or "source" not in record["extra"],
    )

    if settings.log_dir:
        log_dir = settings.log_dir
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        log_suffix = ""
        if settings.fastapi_workers and settings.fastapi_workers > 1:
            log_suffix = f"_{os.getpid()}"
        log_file = os.path.join(log_dir, f'process{log_suffix}.log')
        audit_log = os.path.join(log_dir, f'audit{log_suffix}.log')
        logger.add(
            log_file,
            level=settings.log_level.upper() if settings.log_level else "INFO",
            rotation=settings.log_rotation_size,
            retention=_cleanup_logs,
            compression="gz",
            format=dynamic_format,
            filter=lambda record: len(record["extra"]) == 0 or "source" not in record["extra"]
        )

        logger.add(
            audit_log,
            level="INFO",
            rotation=settings.log_rotation_size,
            retention=_cleanup_logs,
            compression="gz",
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> \x01 "
                   "<level>{level: <8}</level> \x01 "
                   "<cyan>{extra[source]}</cyan> \x01 "
                   "<cyan>{extra[user]}</cyan> \x01 "
                   "<cyan>{extra[result]}</cyan> \x01 "
                   "<cyan>{extra[terminal]}</cyan> \x01 "
                   "<level>{message}</level>",
            filter=lambda record: "source" in record["extra"]
        )


setup_logging()


def _build_va_card(url: str) -> AgentCard:
    card = AgentCard(
        name="VersatileAdapter",
        description="Versatile 低代码平台 A2A 适配器",
        version="1.0.0",
    )
    card.supported_interfaces.append(
        AgentInterface(
            protocol_binding=TransportProtocol.JSONRPC,
            url=url,
            protocol_version=PROTOCOL_VERSION_1_0,
        )
    )
    card.capabilities.CopyFrom(AgentCapabilities(streaming=True))
    return card


def _build_dpa_card() -> AgentCard:
    settings = get_settings()
    host = settings.fastapi_host or "localhost"
    if host == "0.0.0.0":
        host = "localhost"
    port = settings.fastapi_port or 8090
    url = f"http://{host}:{port}/a2a/"
    card = AgentCard(
        name="DPA Service",
        description="EDPA 编排服务：规划并委托 VersatileAdapter 执行子任务",
        version="1.0.0",
    )
    card.supported_interfaces.append(
        AgentInterface(
            protocol_binding=TransportProtocol.JSONRPC,
            url=url,
            protocol_version=PROTOCOL_VERSION_1_0,
        )
    )
    card.capabilities.CopyFrom(AgentCapabilities(streaming=True))
    return card


def _bootstrap_lock_key(lock_name: str) -> str:
    return f"a2a:bootstrap:lock:{lock_name}"


def _bootstrap_status_key(lock_name: str) -> str:
    return f"a2a:bootstrap:status:{lock_name}"


async def _set_bootstrap_status(
    redis: RedisClient,
    *,
    status_key: str,
    status: str,
    owner_id: str,
    message: Optional[str],
    ttl_seconds: int,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "owner_id": owner_id,
        "message": message,
        "update_time": int(time.time()),
    }
    await redis.set_json(status_key, payload, ex=max(int(ttl_seconds), 60))


async def _wait_for_bootstrap_ready(
    redis: RedisClient,
    *,
    status_key: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> bool:
    deadline = time.time() + max(int(timeout_seconds), 1)
    poll = max(float(poll_interval_seconds), 0.2)
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        state = await redis.get_json(status_key) or {}
        status = str(state.get("status") or "").lower()
        if attempts == 1 or attempts % 10 == 0:
            remaining = max(int(deadline - time.time()), 0)
            logger.info(
                "[A2AService] FOLLOWER 等待 bootstrap: attempt={}, status={}, owner={}, remaining={}s",
                attempts,
                status or "<empty>",
                state.get("owner_id"),
                remaining,
            )
        if status == "ready":
            return True
        if status == "failed":
            logger.error("[A2AService] FOLLOWER 检测到 bootstrap 失败: {}", state)
            return False
        await asyncio.sleep(poll)
    return False


async def _run_global_bootstrap_once() -> None:
    logger.info("[A2AService] LEADER 全局 bootstrap 无额外任务，标记为 ready")


class _BootstrapCoordinator:
    """封装 bootstrap 协调流程，降低 lifespan 复杂度。"""

    def __init__(self, *, settings: Any, redis: RedisClient) -> None:
        self.settings = settings
        self.redis = redis

        self.bootstrap_enabled = bool(getattr(settings, "bootstrap_coordination_enabled", False))
        self.bootstrap_lock_name = getattr(settings, "bootstrap_lock_name", "a2a_global_bootstrap")
        self.bootstrap_owner_id = f"{socket.gethostname()}-{os.getpid()}"
        self.bootstrap_lock_key = _bootstrap_lock_key(self.bootstrap_lock_name)
        self.bootstrap_status_key = _bootstrap_status_key(self.bootstrap_lock_name)
        self.bootstrap_lock_ttl = max(int(getattr(settings, "bootstrap_lock_ttl_sec", 180)), 1)
        self.bootstrap_wait_timeout = max(int(getattr(settings, "bootstrap_wait_timeout_sec", 300)), 1)
        self.bootstrap_poll_interval = max(float(getattr(settings, "bootstrap_poll_interval_sec", 1.0)), 0.2)

        # ready 状态用于后启动实例快速放行，TTL 设长一些以避免 leader 退出后状态过早失效。
        self.bootstrap_status_ttl = max(self.bootstrap_wait_timeout * 2, 1800)

        self.leader_locked = False
        self.bootstrap_ready = False

    async def run(self) -> None:
        if not self.bootstrap_enabled:
            logger.info("[A2AService] 已禁用 bootstrap 协调，跳过 Redis leader/follower 编排")
            return

        logger.info(
            "[A2AService] bootstrap 启动参数: lock_name={}, owner={}, ttl={}s, wait_timeout={}s, poll_interval={}s",
            self.bootstrap_lock_name,
            self.bootstrap_owner_id,
            self.bootstrap_lock_ttl,
            self.bootstrap_wait_timeout,
            self.bootstrap_poll_interval,
        )

        self.leader_locked = await self.redis.acquire_lock(
            lock_key=self.bootstrap_lock_key,
            owner_id=self.bootstrap_owner_id,
            ttl_seconds=self.bootstrap_lock_ttl,
        )

        if self.leader_locked:
            await self._run_leader_flow()
        else:
            await self._run_follower_flow()

    async def mark_failed_if_needed(self, exc: Exception) -> None:
        if self.bootstrap_enabled and self.leader_locked and not self.bootstrap_ready:
            try:
                await _set_bootstrap_status(
                    self.redis,
                    status_key=self.bootstrap_status_key,
                    status="failed",
                    owner_id=self.bootstrap_owner_id,
                    message=str(exc),
                    ttl_seconds=self.bootstrap_status_ttl,
                )
            except Exception as mark_exc:
                logger.debug(
                    "[A2AService] 标记 bootstrap failed 失败（忽略）: {}",
                    mark_exc,
                )

    async def close(self) -> None:
        await self._release_leader_lock(reason="service closing")

    async def _run_leader_flow(self) -> None:
        await _set_bootstrap_status(
            self.redis,
            status_key=self.bootstrap_status_key,
            status="initializing",
            owner_id=self.bootstrap_owner_id,
            message="leader is running one-time bootstrap tasks",
            ttl_seconds=self.bootstrap_status_ttl,
        )

        try:
            await _run_global_bootstrap_once()
        except Exception as bootstrap_exc:
            await _set_bootstrap_status(
                self.redis,
                status_key=self.bootstrap_status_key,
                status="failed",
                owner_id=self.bootstrap_owner_id,
                message=str(bootstrap_exc),
                ttl_seconds=self.bootstrap_status_ttl,
            )
            raise

        await _set_bootstrap_status(
            self.redis,
            status_key=self.bootstrap_status_key,
            status="ready",
            owner_id=self.bootstrap_owner_id,
            message="one-time bootstrap finished",
            ttl_seconds=self.bootstrap_status_ttl,
        )
        self.bootstrap_ready = True
        logger.info(
            "[A2AService] 节点角色=LEADER，bootstrap 完成: lock={}, owner={}",
            self.bootstrap_lock_name,
            self.bootstrap_owner_id,
        )
        await self._release_leader_lock(reason="bootstrap finished")

    async def _run_follower_flow(self) -> None:
        logger.info(
            "[A2AService] 节点角色=FOLLOWER，等待 LEADER bootstrap 完成: lock={}, owner={}",
            self.bootstrap_lock_name,
            self.bootstrap_owner_id,
        )
        ready = await _wait_for_bootstrap_ready(
            self.redis,
            status_key=self.bootstrap_status_key,
            timeout_seconds=self.bootstrap_wait_timeout,
            poll_interval_seconds=self.bootstrap_poll_interval,
        )
        if not ready:
            state = await self.redis.get_json(self.bootstrap_status_key)
            raise RuntimeError(f"等待 LEADER bootstrap 完成失败，状态: {state}")
        logger.info("[A2AService] FOLLOWER 检测到 bootstrap ready")

    async def _release_leader_lock(self, *, reason: str) -> None:
        if not (self.bootstrap_enabled and self.leader_locked):
            return
        try:
            released = await self.redis.release_lock(
                lock_key=self.bootstrap_lock_key,
                owner_id=self.bootstrap_owner_id,
            )
            if released:
                logger.info(
                    "[A2AService] LEADER 已释放 bootstrap 锁: lock={}, owner={}, reason={}",
                    self.bootstrap_lock_name,
                    self.bootstrap_owner_id,
                    reason,
                )
        except Exception as release_exc:
            logger.warning("[A2AService] 释放 bootstrap 锁异常: {}", release_exc)
        finally:
            self.leader_locked = False



@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    settings = get_settings()
    redis = RedisClient()
    http_client: Optional[httpx.AsyncClient] = None
    db_handler = None
    bootstrap = _BootstrapCoordinator(settings=settings, redis=redis)

    try:
        await redis.connect(settings.redis_url)
        await bootstrap.run()

        session_ttl = settings.redis_session_ttl or _TTL
        data_store = None

        if settings.runtime_db_enabled:
            data_store, db_handler = await build_runtime_state_store_and_db_handler(
                settings=settings,
                cache_store=redis,
            )
            task_kv = KvAdapter(data_store, namespace="task", default_ttl_seconds=session_ttl)
            session_task_kv = KvAdapter(data_store, namespace="session_task", default_ttl_seconds=session_ttl)
            session_request_kv = KvAdapter(data_store, namespace="session_request", default_ttl_seconds=session_ttl)
            logger.info(
                "[A2AService] KvAdapter 初始化完成：namespace=task/session_task/session_request, "
                "ttl={}s, backend=CacheBackedDataStore(DB权威+Redis缓存)",
                session_ttl,
            )
        else:
            session_task_kv = None
            session_request_kv = None
            db_handler = None
            logger.info(
                "[A2AService] DB持久化已禁用（runtime_db_enabled=false），使用纯Redis模式",
            )

        # 在 initialize() 之前，覆盖 SDK 日志配置（轮转大小、备份数量）
        from openjiuwen.core.common.logging.log_config import configure_log_config
        from openjiuwen.core.common.logging.default.constant import DEFAULT_INNER_LOG_CONFIG
        custom_log_config = DEFAULT_INNER_LOG_CONFIG.copy()
        # 通过 settings 读取，复用 pydantic 校验和默认值，避免 os.getenv 遇到空字符串抛 ValueError
        custom_log_config["backup_count"] = settings.jiuwen_log_backup_count
        custom_log_config["max_bytes"] = settings.jiuwen_log_max_bytes
        configure_log_config(custom_log_config)

        await initialize()
        logger.info("[A2AService] Agent 初始化完成")

        # SDK 日志清理：用 foundation Handler 替换 SDK 默认 Handler，
        # 为 SDK 的 21 个 logger 增加 gzip 压缩 + 按天数清理 + 按总空间清理能力
        from common.sdk_log_cleaner import setup_sdk_log_cleaner
        setup_sdk_log_cleaner()
        logger.info("[A2AService] SDK 日志清理已启用（gzip压缩+按天数+按空间清理）")

        # read=None 关闭 SSE 流读超时：子 Agent 进入并行工作流阶段事件流会出现 >5s 空档
        # （VA 跑批），默认 5s read 超时会误杀子 Agent；整体时长由框架 sub_agent_timeout_seconds
        # （asyncio.wait_for）兜底（见 ref_deploy_a2a_troubleshooting #6）。
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.versatile_adapter_timeout, read=None)
        )
        va_card = _build_va_card(settings.versatile_adapter_url)
        factory = ClientFactory(ClientConfig(httpx_client=http_client))
        va_client = factory.create(va_card)

        # 并行子 Agent 寻址（P-006）：url 由 Agent 自管、随派发请求下传，框架不再从配置读取，
        # 也不在启动期拉取子 Agent AgentCard。改为把 factory 注入 Executor，由其按 spec.url
        # 懒 create_from_url + 缓存（每 url 一个 client）。某子 Agent 不可达只降级该实体。
        if settings.runtime_db_enabled:
            task_store = TaskStoreAdapter(task_kv)
            logger.info("[A2AService] TaskStoreAdapter 初始化完成：底层=KvAdapter(task) → CacheBackedDataStore")
        else:
            task_store = RedisTaskStore(redis, ttl=session_ttl)
            logger.info("[A2AService] RedisTaskStore 初始化完成：纯Redis模式")
        state_manager = TaskStateManager(task_store)
        dpa_card = _build_dpa_card()

        config_path = os.path.join(
            os.path.dirname(__file__), "orchestrator", "config", "route_config.yaml"
        )
        route_dispatcher = RouteDispatcher(
            state_manager,
            config_path=config_path,
            local_agent_names=[dpa_card.name],
        )
        route_dispatcher.register_handlers_from_config(
            state_manager=state_manager,
            va_client=va_client,
            redis=redis,
            client_factory=factory,
            heartbeat_interval_seconds=getattr(settings, "heartbeat_interval_seconds", 15),
            heartbeat_timeout_seconds=getattr(settings, "heartbeat_timeout_seconds", 1800),
            max_concurrent_sub_agents=settings.max_concurrent_sub_agents,
            sub_agent_timeout_seconds=settings.sub_agent_timeout_seconds,
            max_parallel_workflows_per_agent=settings.max_parallel_workflows_per_agent,
            workflow_timeout_seconds=settings.workflow_timeout_seconds,
            max_call_depth=settings.max_call_depth,
            route_dispatcher=route_dispatcher,
            local_agent_name=dpa_card.name,
            session_request_kv=session_request_kv,
        )

        executor = Executor(
            redis=redis,
            route_dispatcher=route_dispatcher,
            state_manager=state_manager,
            heartbeat_interval_seconds=getattr(settings, "heartbeat_interval_seconds", 15),
            heartbeat_timeout_seconds=getattr(settings, "heartbeat_timeout_seconds", 1800),
            session_task_kv=session_task_kv,
            session_request_kv=session_request_kv,
        )

        # SDK DefaultRequestHandler 的 TaskManager 对每个流式事件都调用 task_store.save()，
        # 用 ReadOnlyTaskStore 包装使 SDK 的 save 为空操作，避免数千次全量 Task 序列化写入 Redis。
        # Task 状态持久化由应用层 TaskStateManager 统一管理。
        sdk_task_store = ReadOnlyTaskStore(task_store)
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=sdk_task_store,
            agent_card=dpa_card,
        )

        fastapi_app.state.redis = redis
        fastapi_app.state.db_handler = db_handler
        fastapi_app.state.data_store = data_store
        fastapi_app.state.task_store = task_store
        fastapi_app.state.session_task_kv = session_task_kv
        fastapi_app.state.session_request_kv = session_request_kv
        fastapi_app.state.executor = executor
        fastapi_app.state.adapter_registry = AdapterRegistry.from_yaml(
            os.path.join(os.path.dirname(__file__), "channels.yaml")
        )

        a2a_routes = create_agent_card_routes(dpa_card) + create_jsonrpc_routes(
            request_handler, rpc_url="/"
        )
        fastapi_app.mount("/a2a", Starlette(routes=a2a_routes))

        logger.info(
            f"[A2AService] 启动完成："
            f"VersatileAdapter={settings.versatile_adapter_url}, "
            f"A2A endpoint=http://{settings.fastapi_host or '0.0.0.0'}:{settings.fastapi_port or 8090}/a2a/"
        )
        yield

    except Exception as e:
        logger.error(f"[A2AService] 启动/运行异常: {e}")
        await bootstrap.mark_failed_if_needed(e)
        raise

    finally:
        await bootstrap.close()
        if http_client:
            await http_client.aclose()
        if db_handler:
            try:
                await db_handler.disconnect()
            except Exception as e:
                logger.warning(f"[A2AService] DB关闭异常: {e}")
        await redis.disconnect()
        try:
            from openjiuwen.core.runner import Runner
            await Runner.stop()
        except Exception as stop_exc:
            logger.debug("[A2AService] Runner.stop 异常（忽略）: {}", stop_exc)
        logger.info("[A2AService] 关闭完成")


_TTL = 1800

app = FastAPI(
    title="A2A Service",
    description="DPA + VersatileAdapter 编排服务，支持 Versatile 定制入口和标准 A2A 入口",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(dispatch_router)
app.include_router(simulate_router)


@app.get("/health", tags=["Health"])
async def health_check(success: str = None):
    """服务健康检查"""
    if success is not None:
        return success
    return {
        "status": "healthy",
        "service": "A2A Service",
    }
