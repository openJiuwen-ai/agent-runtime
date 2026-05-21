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
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI
from loguru import logger
from starlette.applications import Starlette

from config import get_settings
from adapter.agent_card import VERSATILE_ADAPTER_CARD
from adapter.executor import VersatileAdapterExecutor
from adapter.versatile_proxy import VersatileProxy


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


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    settings = get_settings()

    versatile_proxy = VersatileProxy(
        url_template=settings.versatile_url_template,
        timeout=settings.versatile_timeout,
        headers_template=settings.versatile_headers_template,
    )

    logger.info(
        f"[VersatileAdapter] Versatile headers template keys: "
        f"{sorted(settings.versatile_headers_template.keys())}"
    )

    task_store = InMemoryTaskStore()

    executor = VersatileAdapterExecutor(
        versatile_proxy=versatile_proxy,
        task_store=task_store,
    )

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

    logger.info(
        f"[VersatileAdapter] 启动完成，"
        f"Versatile URL template: {settings.versatile_url_template}"
    )

    try:
        yield
    finally:
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
