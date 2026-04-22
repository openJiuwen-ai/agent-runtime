"""
A2A Service 进程入口。

暴露端点：
  POST /v1/{project_id}/agents/{agent_id}/conversations/{conv_id}  — 定制化 Versatile 入口
  GET  /a2a/.well-known/agent-card.json                            — A2A 标准 Agent Card
  POST /a2a/                                                        — A2A 标准 JSON-RPC 入口

两条路径共用同一个 Executor + RedisTaskStore，Task 状态一致。

Modified for dependency inversion:
  - New mode: Use create_app() from app_factory.py with injected agent implementation
  - Legacy mode: Directly import from agents.EDPAgent (backward compatible)
  
To enable new mode, set environment variable: USE_FACTORY_MODE=true
"""
from __future__ import annotations

import os
import sys

os.environ['NO_PROXY'] = 'localhost,127.0.0.1'

USE_FACTORY_MODE = os.environ.get("USE_FACTORY_MODE", "false").lower() == "true"

if USE_FACTORY_MODE:
    from app_factory import create_app, setup_logging
    from protocol import AgentInitializer, AgentStreamFunc
    
    setup_logging()
    
    def get_legacy_agent_impl():
        from agents.EDPAgent import initialize, agent_stream
        return initialize, agent_stream
    
    _agent_initializer: AgentInitializer | None = None
    _agent_stream_func: AgentStreamFunc | None = None
    
    def set_agent_impl(initializer: AgentInitializer, stream_func: AgentStreamFunc):
        global _agent_initializer, _agent_stream_func
        _agent_initializer = initializer
        _agent_stream_func = stream_func
    
    if _agent_initializer is None or _agent_stream_func is None:
        try:
            _agent_initializer, _agent_stream_func = get_legacy_agent_impl()
        except ImportError:
            pass
    
    if _agent_initializer and _agent_stream_func:
        app = create_app(
            agent_initializer=_agent_initializer,
            agent_stream_func=_agent_stream_func,
            include_test_routes=True,
        )
    else:
        from fastapi import FastAPI
        from loguru import logger
        app = FastAPI(title="A2A Service - Waiting for Agent")
        logger.warning("[A2AService] USE_FACTORY_MODE=true but no agent implementation injected")
        
else:
    import httpx
    from contextlib import asynccontextmanager
    
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
    from config import get_settings
    from orchestrator.executor import Executor
    from common.redis_task_store import RedisTaskStore
    from orchestrator.user_router import router as user_router
    
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
    
    _TTL = 1800
    
    @asynccontextmanager
    async def lifespan_legacy(app: FastAPI):
        from agents.EDPAgent import agent_stream
        
        settings = get_settings()
    
        redis = RedisClient()
        await redis.connect(settings.redis_url)
    
        await initialize()
        logger.info("[A2AService] Agent 初始化完成")
    
        http_client = httpx.AsyncClient()
        va_card = _build_va_card(settings.versatile_adapter_url)
        factory = ClientFactory(ClientConfig(httpx_client=http_client))
        va_client = factory.create(va_card)
    
        task_store = RedisTaskStore(redis, ttl=settings.redis_session_ttl or _TTL)
        executor = Executor(va_client=va_client, redis=redis, task_store=task_store, agent_stream_func=agent_stream)
    
        dpa_card = _build_dpa_card()
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
            f"A2A endpoint=http://{settings.fastapi_host or '0.0.0.0'}:{settings.fastapi_port or 8090}/a2a/"
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
        title="A2A Service (Legacy Mode)",
        description="DPA + VersatileAdapter 编排服务，支持 Versatile 定制入口和标准 A2A 入口",
        version="1.0.0",
        lifespan=lifespan_legacy,
    )
    
    app.include_router(user_router)
    
    from test.simulate import router as simulate_router
    app.include_router(simulate_router)
