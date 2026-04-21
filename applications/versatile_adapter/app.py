"""
VersatileAdapter 进程入口（a2a-sdk 1.0.0-alpha.1）。

启动方式：
  cd agent-runtime/applications/versatile_adapter
  python main.py

暴露端点（A2A SDK 标准）：
  GET  /.well-known/agent-card.json  — AgentCard
  POST /                             — A2A JSON-RPC（message/send、message/stream）

本服务仅供 a2a_service 内部调用，不直接面向用户。

Modified for dependency inversion:
  - New mode: Use create_adapter_app() from app_factory.py
  - Legacy mode: Direct lifespan (backward compatible)
  
To enable new mode, set environment variable: USE_FACTORY_MODE=true
"""
from __future__ import annotations

import os
import sys

os.environ['NO_PROXY'] = 'localhost,127.0.0.1'

USE_FACTORY_MODE = os.environ.get("USE_FACTORY_MODE", "false").lower() == "true"

if USE_FACTORY_MODE:
    from app_factory import create_adapter_app, setup_logging
    
    setup_logging()
    
    app = create_adapter_app()
    
else:
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
    
    def setup_logging() -> None:
        settings = get_settings()
    
        logger.remove()
    
        logger.add(
            sys.stderr,
            level=settings.log_level.upper() if settings.log_level else "INFO",
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                   "<level>{level: <8}</level> | "
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                   "<level>{message}</level>"
        )
    
        if settings.log_file:
            log_dir = os.path.dirname(settings.log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            log_file_path = settings.log_file
            base, ext = os.path.splitext(log_file_path)
            log_file_with_pid = f"{base}_{os.getpid()}{ext}"
            logger.add(
                log_file_with_pid,
                level=settings.log_level.upper() if settings.log_level else "INFO",
                rotation="100 MB",
                retention="7 days",
                compression="gz",
                format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                       "<level>{level: <8}</level> | "
                       "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                       "<level>{message}</level>"
            )
    
        logger.configure(extra={"trace_id": "default_trace_id"})
    
    setup_logging()
    
    @asynccontextmanager
    async def lifespan_legacy(app: FastAPI):
        settings = get_settings()
    
        versatile_proxy = VersatileProxy(
            url_template=settings.versatile_url_template,
            timeout=settings.versatile_timeout,
        )
    
        executor = VersatileAdapterExecutor(
            versatile_proxy=versatile_proxy,
        )
    
        task_store = InMemoryTaskStore()
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=task_store,
            agent_card=VERSATILE_ADAPTER_CARD,
        )
        a2a_routes = (
            create_agent_card_routes(VERSATILE_ADAPTER_CARD)
            + create_jsonrpc_routes(request_handler, rpc_url="/")
        )
        app.mount("/", Starlette(routes=a2a_routes))
    
        logger.info(
            f"[VersatileAdapter] 启动完成，"
            f"Versatile URL template: {settings.versatile_url_template}"
        )
    
        try:
            yield
        finally:
            logger.info("[VersatileAdapter] 关闭完成")
    
    app = FastAPI(
        title="VersatileAdapter (Legacy Mode)",
        description="Versatile 低代码平台 A2A 适配器",
        version="1.0.0",
        lifespan=lifespan_legacy,
    )
