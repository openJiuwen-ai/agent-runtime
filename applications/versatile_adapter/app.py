# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileAdapter 进程入口（a2a-sdk 1.0.0-alpha.1）。

启动方式：
  cd agent-runtime/applications/versatile_adapter
  python main.py

暴露端点（A2A SDK 标准）：
  GET  /.well-known/agent-card.json  — AgentCard
  POST /                             — A2A JSON-RPC（message/send、message/stream）

本服务仅供 a2a_service 内部调用，不直接面向用户。
"""
from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from fastapi import FastAPI
from loguru import logger
from starlette.applications import Starlette

from a2a.server.tasks import InMemoryTaskStore, TaskStore
from persistence.redis_client import RedisClient
from persistence.redis_task_store import RedisTaskStore
from config import get_settings
from a2a_facade.agent_card import VERSATILE_ADAPTER_CARD
from dispatcher.runner import VersatileAdapterRunner
from a2a_facade.executor import A2aVersatileExecutor


os.environ['NO_PROXY'] = 'localhost,127.0.0.1'


def dynamic_format(record) -> str:
    if len(record["extra"]) == 0:
        return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> \x01 " \
                   "<level>{level: <8}</level> \x01 " \
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> \x01 " \
                   "<level>{message}</level> \n"
    elif "conv_id" in record["extra"]:
        return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> \x01 " \
                   "<level>{level: <8}</level> \x01 " \
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> \x01 " \
                   "<cyan>{extra[trace_id]}</cyan> \x01 " \
                   "<cyan>{extra[agent_id]}</cyan> \x01 " \
                   "<cyan>{extra[conv_id]}</cyan> \x01 " \
                   "<level>{message}</level>\n"
    else:
        return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> \x01 " \
                   "<level>{level: <8}</level> \x01 " \
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> \x01 " \
                   "<cyan>{extra[trace_id]}</cyan> \x01 " \
                   "<level>{message}</level>\n"


def setup_logging() -> None:
    """配置日志"""
    settings = get_settings()

    logger.remove()

    logger.add(
        sys.stderr,
        level=settings.adapter_log_level.upper() if settings.adapter_log_level else "INFO",
        format=dynamic_format,
        filter=lambda record: len(record["extra"]) == 0 or "trace_id" in record["extra"]
    )

    if settings.adapter_log_file:
        log_dir = os.path.dirname(settings.adapter_log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        log_file_path = settings.adapter_log_file
        base, ext = os.path.splitext(log_file_path)
        log_file_with_pid = f"{base}_{os.getpid()}{ext}"
        logger.add(
            log_file_with_pid,
            level=settings.adapter_log_level.upper() if settings.adapter_log_level else "INFO",
            rotation="100 MB",
            retention="7 days",
            compression="gz",
            format=dynamic_format,
            filter=lambda record: len(record["extra"]) == 0 or "trace_id" in record["extra"]
        )


    logger.info(
        f"[VersatileAdapter] 日志初始化完成 "
        f"level={settings.adapter_log_level or 'INFO'} "
        f"file={settings.adapter_log_file or '-'}"
    )


setup_logging()


_TTL = 1800


async def _create_task_store(settings) -> tuple[TaskStore, RedisClient | None]:
    """根据配置创建 TaskStore：Redis 有效时使用 RedisTaskStore，否则回退 InMemoryTaskStore。

    Returns:
        (task_store, redis_client) — redis_client 在 Redis 模式下非 None，调用方需在关闭时 disconnect。
    """
    if settings.redis_host:
        redis = RedisClient()
        await redis.connect(settings.redis_url)
        task_store = RedisTaskStore(redis, ttl=settings.redis_session_ttl or _TTL)
        logger.info(f"[VersatileAdapter] TaskStore=RedisTaskStore, host={settings.redis_host}")
        return task_store, redis
    logger.info("[VersatileAdapter] TaskStore=InMemoryTaskStore（未配置 Redis）")
    return InMemoryTaskStore(), None


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    settings = get_settings()

    # 1. 创建 TaskStore（按配置选择 Redis 或 InMemory）
    task_store, redis = await _create_task_store(settings)

    # 2. 从 YAML 配置创建 Runner（动态路由）
    runner = VersatileAdapterRunner()

    # 3. 创建 A2A 薄壳
    executor = A2aVersatileExecutor(runner=runner)

    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=VERSATILE_ADAPTER_CARD,
    )
    a2a_routes = (
        create_agent_card_routes(VERSATILE_ADAPTER_CARD)
        + create_jsonrpc_routes(request_handler, rpc_url="/")
    )
    fastapi_app.mount("/", Starlette(routes=a2a_routes))

    logger.info("[VersatileAdapter] 启动完成")

    try:
        yield
    finally:
        if redis:
            await redis.disconnect()
        logger.info("[VersatileAdapter] 关闭完成")


app = FastAPI(
    title="VersatileAdapter",
    description="Versatile 低代码平台 A2A 适配器",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def inject_trace_id(request, call_next):
    trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
    logger.debug(f"接收到请求: {request.method} {request.url}，trace_id={trace_id}")
    with logger.contextualize(trace_id=trace_id):
        response = await call_next(request)
    response.headers["x-trace-id"] = trace_id
    return response


@app.get("/health", tags=["Health"])
async def health_check():
    """服务健康检查"""
    logger.debug("[VersatileAdapter] health check")
    return {
        "status": "healthy",
        "service": "VersatileAdapter",
    }
