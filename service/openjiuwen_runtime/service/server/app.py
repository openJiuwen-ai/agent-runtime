# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""App —— 对外入口（设计 §6.1、§7）。

持有内部 ``MessageRouter``，构造时把 REST/WS 适配器挂到该 router；对外暴露
``@app.handle`` / ``@app.stream`` / ``app.use`` / ``app.dispatch`` / ``app.asgi`` / ``app.run``。
仅此层（及适配器）import fastapi/websockets，核心代码零 HTTP/WS 导入。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI
from pydantic import BaseModel

from ..context.system_context import SystemContext
from ..envelope import Envelope
from ..routing.router import MessageRouter
from ..routing.result import DispatchResult
from .rest_adapter import mount_rest
from .ws_adapter import mount_ws


async def _ensure_sysctx_async(
    fastapi_app: FastAPI, ctx_factory: Callable[[], SystemContext]
) -> SystemContext:
    """取得当前 sysctx：lifespan 已建则复用，否则惰性建（兼容不跑 lifespan 的 httpx ASGITransport）。"""
    sysctx = getattr(fastapi_app.state, "sysctx", None)
    if sysctx is None:
        sysctx = ctx_factory()
        await sysctx.start()
        fastapi_app.state.sysctx = sysctx
    return sysctx


def _build_fastapi(
    router: MessageRouter,
    ctx_factory: Callable[[], SystemContext],
    prefix: str,
    enable_rest: bool,
    enable_ws: bool,
    title: str,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        # 生产 / TestClient 走 lifespan：进程级 sysctx 在此创建与释放
        sysctx = ctx_factory()
        await sysctx.start()
        fastapi_app.state.sysctx = sysctx
        try:
            yield
        finally:
            await sysctx.stop()

    fastapi = FastAPI(title=title, lifespan=lifespan)

    async def ensure(fastapi_app: FastAPI) -> SystemContext:
        return await _ensure_sysctx_async(fastapi_app, ctx_factory)

    if enable_rest:
        mount_rest(fastapi, router, prefix, ensure)
    if enable_ws:
        mount_ws(fastapi, router, ensure)
    return fastapi


class App:
    """对外入口类。"""

    def __init__(
        self,
        ctx_factory: Callable[[], SystemContext],
        *,
        prefix: str = "/api",
        enable_rest: bool = True,
        enable_ws: bool = True,
        title: str = "service",
    ) -> None:
        self._router = MessageRouter()
        self._ctx_factory = ctx_factory
        self._prefix = prefix.rstrip("/") if prefix else ""
        self._fastapi = _build_fastapi(
            self._router, ctx_factory, self._prefix, enable_rest, enable_ws, title
        )

    # ------------------------------------------------------------ 注册委托
    def handle(self, msg_type: str, *, request_model: type[BaseModel] | None = None):
        """非流式 handler 装饰器（委托 router）。"""
        return self._router.handle(msg_type, request_model=request_model)

    def stream(self, msg_type: str, *, request_model: type[BaseModel] | None = None):
        """流式 handler 装饰器（委托 router）。"""
        return self._router.stream(msg_type, request_model=request_model)

    def use(self, middleware) -> None:
        """中间件（委托 router）。"""
        self._router.use(middleware)

    # ------------------------------------------------------------ 派发
    async def dispatch(self, env: Envelope, rctx: Any) -> DispatchResult:
        """核心派发（传输无关）。适配器与直调共用此路径。"""
        return await self._router.dispatch(env, rctx)

    # ------------------------------------------------------------ 传输
    @property
    def asgi(self) -> FastAPI:
        """底层 ASGI，供 TestClient/httpx 测试与 uvicorn 部署。"""
        return self._fastapi

    def run(self, host: str | None = None, port: int | None = None, **kwargs: Any) -> None:
        """uvicorn 部署；多副本：不同端口起多实例，共享 redis。

        host/port 缺省取自环境变量 ``OPENJIUWEN_SERVICE_HOST`` / ``OPENJIUWEN_SERVICE_PORT``；
        显式传参则覆盖环境变量。
        """
        import uvicorn

        from ..config import ServiceConfig

        cfg = ServiceConfig.from_env()
        host = cfg.host if host is None else host
        port = cfg.port if port is None else port
        uvicorn.run(self._fastapi, host=host, port=port, **kwargs)
