"""
VersatileAdapter Factory for Dependency Inversion.

This module provides create_adapter_app() factory function for creating
VersatileAdapter service, enabling dependency inversion pattern.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.applications import Starlette

from config import get_settings
from adapter.agent_card import VERSATILE_ADAPTER_CARD
from adapter.executor import VersatileAdapterExecutor
from adapter.versatile_proxy import VersatileProxy


os.environ['NO_PROXY'] = 'localhost,127.0.0.1'


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


def create_adapter_app(
    url_template: str | None = None,
    timeout: int | None = None,
) -> FastAPI:
    """
    Factory function to create VersatileAdapter FastAPI application.
    
    Args:
        url_template: Versatile platform URL template (overrides config)
        timeout: Request timeout in seconds (overrides config)
        
    Returns:
        FastAPI application ready to run
        
    Example:
        from agent_runtime.versatile_adapter import create_adapter_app
        
        app = create_adapter_app(
            url_template="https://versatile.example/api/{conv_id}",
            timeout=600,
        )
        uvicorn.run(app, host="0.0.0.0", port=8091)
    """
    setup_logging()
    
    settings = get_settings()
    url_template = url_template or settings.versatile_url_template
    timeout = timeout or settings.versatile_timeout

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        versatile_proxy = VersatileProxy(
            url_template=url_template,
            timeout=timeout,
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
            f"Versatile URL template: {url_template}"
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

    @app.get("/health", tags=["Health"])
    async def health_check():
        """Health check endpoint for monitoring and load balancers."""
        return JSONResponse(content={"status": "healthy", "service": "versatile_adapter"})

    return app