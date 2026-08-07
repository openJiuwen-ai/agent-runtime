# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Public application entry point for the transport-neutral service runtime."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable, Iterable

from fastapi import FastAPI
from pydantic import BaseModel

from ..context.system_context import SystemContext
from ..envelope import Envelope
from ..routing.handlers import (
    FunctionMessageHandler,
    FunctionStreamMessageHandler,
    HandlerModule,
    HandlerSpec,
    MessageHandler,
    StreamMessageHandler,
)
from ..routing.result import DispatchResult
from ..routing.router import MessageRouter
from ..security import OAuth2AccessControl
from .rest_adapter import RestAdapter
from .ws_adapter import mount_ws


async def _ensure_sysctx_async(
    fastapi_app: FastAPI,
    ctx_factory: Callable[[], SystemContext],
) -> SystemContext:
    """Return the lifespan context or lazily create one for ASGI transports."""
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
    oauth2: OAuth2AccessControl | None,
) -> tuple[FastAPI, RestAdapter | None]:
    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
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

    rest_adapter = None
    if enable_rest:
        rest_adapter = RestAdapter(fastapi, router, prefix, ensure, oauth2)
    if enable_ws:
        mount_ws(fastapi, router, ensure)
    return fastapi, rest_adapter


class App:
    """Compose handlers, middleware, transports, and process-level context."""

    def __init__(
        self,
        ctx_factory: Callable[[], SystemContext],
        *,
        prefix: str = "/api",
        enable_rest: bool = True,
        enable_ws: bool = True,
        title: str = "service",
        oauth2: OAuth2AccessControl | None = None,
    ) -> None:
        self._router = MessageRouter()
        self._ctx_factory = ctx_factory
        self._prefix = prefix.rstrip("/") if prefix else ""
        self._fastapi, self._rest_adapter = _build_fastapi(
            self._router,
            ctx_factory,
            self._prefix,
            enable_rest,
            enable_ws,
            title,
            oauth2,
        )

    def handle(
        self,
        msg_type: str,
        *,
        request_model: type[BaseModel] | None = None,
        response_model: type[BaseModel] | None = None,
        summary: str | None = None,
        description: str | None = None,
        tags: Iterable[str] | None = None,
    ):
        """Register an async function; shorthand for :meth:`register`."""
        spec = HandlerSpec(
            msg_type=msg_type,
            request_model=request_model,
            response_model=response_model,
            summary=summary,
            description=description,
            tags=tuple(tags or ()),
        )

        def decorator(fn):
            self.register(FunctionMessageHandler(spec, fn))
            return fn

        return decorator

    def stream(
        self,
        msg_type: str,
        *,
        request_model: type[BaseModel] | None = None,
        summary: str | None = None,
        description: str | None = None,
        tags: Iterable[str] | None = None,
    ):
        """Register an async generator; shorthand for :meth:`register`."""
        spec = HandlerSpec(
            msg_type=msg_type,
            request_model=request_model,
            summary=summary,
            description=description,
            tags=tuple(tags or ()),
        )

        def decorator(fn):
            self.register(FunctionStreamMessageHandler(spec, fn))
            return fn

        return decorator

    def register(
        self,
        handler: MessageHandler[Any] | StreamMessageHandler[Any],
    ) -> "App":
        """Register one reusable handler and expose it through enabled adapters."""
        self._router.register(handler)
        if self._rest_adapter is not None:
            self._rest_adapter.register(handler)
        return self

    def register_all(
        self,
        handlers: Iterable[MessageHandler[Any] | StreamMessageHandler[Any]],
    ) -> "App":
        """Register multiple reusable handlers in order."""
        for handler in handlers:
            self.register(handler)
        return self

    def include(self, module: HandlerModule) -> "App":
        """Include every handler contributed by a reusable handler module."""
        handlers = getattr(module, "handlers", None)
        if not callable(handlers):
            raise TypeError("module must provide a handlers() method")
        return self.register_all(handlers())

    def use(self, middleware) -> None:
        """Register transport-neutral router middleware."""
        self._router.use(middleware)

    async def dispatch(self, env: Envelope[Any], rctx: Any) -> DispatchResult:
        """Dispatch directly through the same router used by all adapters."""
        return await self._router.dispatch(env, rctx)

    @property
    def asgi(self) -> FastAPI:
        """Expose the underlying ASGI app for deployment and testing."""
        return self._fastapi

    def run(
        self,
        host: str | None = None,
        port: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Run with uvicorn, using ServiceConfig defaults when omitted."""
        import uvicorn

        from ..config import ServiceConfig

        config = ServiceConfig.from_env()
        host = config.host if host is None else host
        port = config.port if port is None else port
        uvicorn.run(self._fastapi, host=host, port=port, **kwargs)
