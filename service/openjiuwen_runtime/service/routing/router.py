# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""MessageRouter：type→handler 注册表 + 中间件链 + dispatch（设计 §6.3）。

- 注册表取代 if/elif：type → handler 的 O(1) 查表。
- 唯一性约束：同一 type 重复注册抛错；同一 type 不能同时流式与非流式。
- 中间件链：洋葱模型，先注册为外层；handler 是链尾。
- v1 仅精确匹配，不做通配/前缀路由。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

from ..envelope import Envelope, ResponseEnvelope, StreamChunk
from ..errors import ErrorCode, FrameworkError, NotFoundError, ValidationError, exception_code
from .handlers import Middleware
from .result import DispatchResult, StreamResult, UnaryResult

logger = logging.getLogger(__name__)

_UNARY = "unary"
_STREAM = "stream"


@dataclass
class _Endpoint:
    handler: Any
    kind: str  # _UNARY | _STREAM


class MessageRouter:
    """App 内部实现，不对用户直接暴露。"""

    def __init__(self) -> None:
        self._endpoints: dict[str, _Endpoint] = {}
        self._middleware: list[Middleware] = []

    # ------------------------------------------------------------------ 注册
    def handle(self, msg_type: str):
        """非流式 handler 装饰器。"""

        def decorator(fn):
            self._register(msg_type, fn, _UNARY)
            return fn

        return decorator

    def stream(self, msg_type: str):
        """流式 handler 装饰器。"""

        def decorator(fn):
            self._register(msg_type, fn, _STREAM)
            return fn

        return decorator

    def use(self, middleware: Middleware) -> None:
        """注册中间件（先注册为外层）。"""
        self._middleware.append(middleware)

    def has(self, msg_type: str) -> bool:
        return msg_type in self._endpoints

    def kinds(self) -> dict[str, str]:
        """type → kind 视图（适配器据此区分流式/非流式）。"""
        return {t: ep.kind for t, ep in self._endpoints.items()}

    def _register(self, msg_type: str, handler: Any, kind: str) -> None:
        if not msg_type:
            raise ValidationError("msg_type must be a non-empty string")
        existing = self._endpoints.get(msg_type)
        if existing is not None:
            raise FrameworkError(
                f"type {msg_type!r} already registered as {existing.kind}",
                code=ErrorCode.CONFLICT,
            )
        self._endpoints[msg_type] = _Endpoint(handler=handler, kind=kind)

    # ------------------------------------------------------------------ 派发
    async def dispatch(self, env: Envelope, rctx: Any) -> DispatchResult:
        endpoint = self._endpoints.get(env.type)
        if endpoint is None:
            return UnaryResult(response=self._error_response(
                env, NotFoundError(f"no handler registered for type {env.type!r}")))

        core = self._build_core(endpoint)
        chain = self._compose(self._middleware, core)
        try:
            return await chain(rctx, env)
        except FrameworkError as exc:
            return UnaryResult(response=self._error_response(env, exc))
        except Exception as exc:  # noqa: BLE001 - 归一化为 internal 错误信封
            logger.exception("dispatch failed: type=%s request_id=%s", env.type, env.metadata.request_id)
            return UnaryResult(response=self._error_response(env, FrameworkError(str(exc))))

    # -------------------------------------------------------------- 核心组装
    def _build_core(self, endpoint: _Endpoint):
        if endpoint.kind == _UNARY:
            return self._unary_core(endpoint.handler)
        return self._stream_core(endpoint.handler)

    def _unary_core(self, handler):
        async def core(ctx, env):
            result = await handler(ctx, env)
            return UnaryResult(response=self._normalize_unary(result, env))

        return core

    def _stream_core(self, handler):
        async def core(ctx, env):
            ait = handler(ctx, env)  # async generator → async iterator（同步调用）
            return StreamResult(chunks=self._wrap_stream(ait, env))

        return core

    @staticmethod
    def _compose(middlewares: list[Middleware], core):
        fn = core
        for mw in reversed(middlewares):  # 先注册为外层
            fn = MessageRouter._wrap_one(mw, fn)
        return fn

    @staticmethod
    def _wrap_one(mw, nxt):
        async def wrapped(ctx, env):
            return await mw(ctx, env, nxt)

        return wrapped

    # -------------------------------------------------------------- 归一化
    @staticmethod
    def _normalize_unary(result: Any, env: Envelope) -> ResponseEnvelope:
        if isinstance(result, ResponseEnvelope):
            return result
        if isinstance(result, dict):
            return ResponseEnvelope(type=env.type, metadata=env.metadata,
                                     rawdata=result, ok=True)
        raise FrameworkError(
            f"unary handler must return dict or ResponseEnvelope, got {type(result).__name__}")

    def _wrap_stream(self, ait: AsyncIterator, env: Envelope) -> AsyncIterator[StreamChunk]:
        """为流式 handler 的产物分配 sequence、置末帧 is_final；出错发末帧错误分片。"""

        async def gen():
            seq = 0
            it = ait.__aiter__()
            try:
                try:
                    nxt = await it.__anext__()
                except StopAsyncIteration:
                    return
                while True:
                    cur = nxt
                    try:
                        nxt = await it.__anext__()
                    except StopAsyncIteration:
                        seq += 1
                        yield self._to_chunk(cur, seq, is_final=True, env=env)
                        return
                    seq += 1
                    yield self._to_chunk(cur, seq, is_final=False, env=env)
            except FrameworkError as exc:
                seq += 1
                yield self._error_chunk(seq, env, exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("stream handler failed: type=%s", env.type)
                seq += 1
                yield self._error_chunk(seq, env, FrameworkError(str(exc)))

        return gen()

    @staticmethod
    def _to_chunk(item: Any, sequence: int, is_final: bool, env: Envelope) -> StreamChunk:
        if isinstance(item, StreamChunk):
            return StreamChunk(sequence=sequence, is_final=is_final, metadata=env.metadata,
                               rawdata=item.rawdata, error_code=item.error_code,
                               error_message=item.error_message)
        if isinstance(item, dict):
            return StreamChunk(sequence=sequence, is_final=is_final, metadata=env.metadata,
                               rawdata=item)
        raise FrameworkError(
            f"stream handler must yield dict or StreamChunk, got {type(item).__name__}")

    # -------------------------------------------------------------- 错误信封
    def _error_response(self, env: Envelope, exc: FrameworkError) -> ResponseEnvelope:
        return ResponseEnvelope(
            type=env.type, metadata=env.metadata, rawdata={},
            ok=False, error_code=exception_code(exc), error_message=exc.message)

    def _error_chunk(self, sequence: int, env: Envelope, exc: FrameworkError) -> StreamChunk:
        return StreamChunk(
            sequence=sequence, is_final=True, metadata=env.metadata, rawdata={},
            error_code=exception_code(exc), error_message=exc.message)
