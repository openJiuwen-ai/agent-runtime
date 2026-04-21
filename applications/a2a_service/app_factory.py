"""
A2A Service Factory for Dependency Inversion.

This module provides create_app() factory function that accepts Agent implementation
as parameters, enabling dependency inversion where Agent imports runtime and injects
its own implementation.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard as A2AAgentCard, AgentInterface
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.applications import Starlette

from common.redis_client import RedisClient
from config import get_settings
from orchestrator.executor import Executor
from common.redis_task_store import RedisTaskStore
from orchestrator.user_router import router as user_router
from protocol import AgentInitializer, AgentStreamFunc


os.environ['NO_PROXY'] = 'localhost,127.0.0.1'

_TTL = 1800


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


def _build_va_card(url: str) -> A2AAgentCard:
    card = A2AAgentCard(
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


def _build_dpa_card(host: str, port: int, agent_name: str = "DPA Service") -> A2AAgentCard:
    if host == "0.0.0.0":
        host = "localhost"
    url = f"http://{host}:{port}/a2a/"
    card = A2AAgentCard(
        name=agent_name,
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


def create_app(
    agent_initializer: AgentInitializer,
    agent_stream_func: AgentStreamFunc,
    agent_name: str = "DPA Service",
    include_test_routes: bool = False,
) -> FastAPI:
    """
    Factory function to create A2A Service FastAPI application.
    
    This enables dependency inversion: Agent imports this factory and injects
    its own implementation, rather than runtime hardcoding Agent import.
    
    Args:
        agent_initializer: Async function called once at startup
        agent_stream_func: Async generator function for agent stream
        agent_name: Name for agent card (default "DPA Service")
        include_test_routes: Whether to include test/simulate routes
        
    Returns:
        FastAPI application ready to run
        
    Example:
        from agent_runtime.a2a_service import create_app
        from my_agent import initialize, agent_stream
        
        app = create_app(
            agent_initializer=initialize,
            agent_stream_func=agent_stream,
        )
        uvicorn.run(app, host="0.0.0.0", port=8090)
    """
    setup_logging()
    
    settings = get_settings()
    host = settings.fastapi_host or "0.0.0.0"
    port = settings.fastapi_port or 8090
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis = RedisClient()
        await redis.connect(settings.redis_url)

        await agent_initializer()
        logger.info("[A2AService] Agent 初始化完成")

        http_client = httpx.AsyncClient()
        va_card = _build_va_card(settings.versatile_adapter_url)
        factory = ClientFactory(ClientConfig(httpx_client=http_client))
        va_client = factory.create(va_card)

        task_store = RedisTaskStore(redis, ttl=settings.redis_session_ttl or _TTL)
        executor = Executor(
            va_client=va_client,
            redis=redis,
            task_store=task_store,
            agent_stream_func=agent_stream_func,
        )

        dpa_card = _build_dpa_card(host, port, agent_name)
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=task_store,
            agent_card=dpa_card,
        )

        app.state.redis = redis
        app.state.task_store = task_store
        app.state.executor = executor

        a2a_routes = create_agent_card_routes(dpa_card) + create_jsonrpc_routes(
            request_handler, rpc_url="/"
        )
        app.mount("/a2a", Starlette(routes=a2a_routes))

        logger.info(
            f"[A2AService] 启动完成："
            f"VersatileAdapter={settings.versatile_adapter_url}, "
            f"A2A endpoint=http://{host}:{port}/a2a/"
        )

        try:
            yield
        finally:
            await redis.disconnect()
            await http_client.aclose()
            try:
                from openjiuwen.core.runner import Runner
                await Runner.stop()
            except Exception:
                pass
            logger.info("[A2AService] 关闭完成")

    app = FastAPI(
        title="A2A Service",
        description=f"{agent_name} + VersatileAdapter 编排服务",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["Health"])
    async def health_check():
        """Health check endpoint for monitoring and load balancers."""
        return JSONResponse(content={"status": "healthy", "service": "a2a_service"})

    app.include_router(user_router)
    
    if include_test_routes:
        from test.simulate import router as simulate_router
        app.include_router(simulate_router)

    return app