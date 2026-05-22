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
from common.redis_task_store import RedisTaskStore
from config import get_settings
from orchestrator.executor import Executor
from orchestrator.user_router import router as user_router
from tools.simulate_router.simulate import router as simulate_router

os.environ['NO_PROXY'] = 'localhost,127.0.0.1'


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
        log_file_with_pid = f"{log_dir}{os.sep}/process_{os.getpid()}.log"
        audit_log = f"{log_dir}{os.sep}/audit_{os.getpid()}.log"
        logger.add(
            log_file_with_pid,
            level=settings.log_level.upper() if settings.log_level else "INFO",
            rotation="20 MB",
            retention="7 days",
            compression="gz",
            format=dynamic_format,
            filter=lambda record: len(record["extra"]) == 0 or "source" not in record["extra"]
        )

        logger.add(
            audit_log,
            level="INFO",
            rotation="20 MB",
            retention="30 days",
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
    bootstrap = _BootstrapCoordinator(settings=settings, redis=redis)

    try:
        await redis.connect(settings.redis_url)
        await bootstrap.run()

        await initialize()
        logger.info("[A2AService] Agent 初始化完成")

        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.versatile_adapter_timeout, read=None)
        )
        va_card = _build_va_card(settings.versatile_adapter_url)
        factory = ClientFactory(ClientConfig(httpx_client=http_client))
        va_client = factory.create(va_card)

        task_store = RedisTaskStore(redis, ttl=settings.redis_session_ttl or _TTL)
        executor = Executor(va_client=va_client, redis=redis, task_store=task_store)

        dpa_card = _build_dpa_card()
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=task_store,
            agent_card=dpa_card,
        )

        fastapi_app.state.redis = redis
        fastapi_app.state.task_store = task_store
        fastapi_app.state.executor = executor

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

app.include_router(user_router)
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
