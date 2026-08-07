# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Message type registry, middleware chain, and transport-neutral dispatch."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable

from pydantic import ValidationError as PydanticValidationError

from ..envelope import Envelope, ResponseEnvelope, StreamChunk
from ..errors import (
    DeadlineExceeded,
    ErrorCode,
    FrameworkError,
    NotFoundError,
    ValidationError,
    exception_code,
)
from .handlers import (
    FunctionMessageHandler,
    FunctionStreamMessageHandler,
    HandlerSpec,
    MessageHandler,
    Middleware,
    StreamMessageHandler,
    _make_spec,
    _validate_handler,
)
from .result import DispatchResult, StreamResult, UnaryResult

logger = logging.getLogger(__name__)

_UNARY = "unary"
_STREAM = "stream"


@dataclass
class _Endpoint:
    handler: MessageHandler[Any] | StreamMessageHandler[Any]
    kind: str


class MessageRouter:
    """Map exact message types to handlers and execute the middleware chain."""

    def __init__(self) -> None:
        self._endpoints: dict[str, _Endpoint] = {}
        self._middleware: list[Middleware] = []

    def handle(
        self,
        msg_type: str,
        *,
        request_model=None,
        response_model=None,
        summary: str | None = None,
        description: str | None = None,
        tags: Iterable[str] | None = None,
    ):
        """Register an async non-streaming function."""
        spec = _make_spec(
            msg_type,
            request_model=request_model,
            response_model=response_model,
            summary=summary,
            description=description,
            tags=tags,
        )

        def decorator(fn):
            self.register(FunctionMessageHandler(spec, fn))
            return fn

        return decorator

    def stream(
        self,
        msg_type: str,
        *,
        request_model=None,
        summary: str | None = None,
        description: str | None = None,
        tags: Iterable[str] | None = None,
    ):
        """Register an async-generator function."""
        spec = _make_spec(
            msg_type,
            request_model=request_model,
            summary=summary,
            description=description,
            tags=tags,
        )

        def decorator(fn):
            self.register(FunctionStreamMessageHandler(spec, fn))
            return fn

        return decorator

    def register(
        self,
        handler: MessageHandler[Any] | StreamMessageHandler[Any],
    ) -> "MessageRouter":
        """Register one object-oriented unary or streaming handler."""
        _validate_handler(handler)
        msg_type = handler.spec.msg_type
        existing = self._endpoints.get(msg_type)
        if existing is not None:
            raise FrameworkError(
                f"type {msg_type!r} already registered as {existing.kind}",
                code=ErrorCode.CONFLICT,
            )
        kind = _STREAM if isinstance(handler, StreamMessageHandler) else _UNARY
        self._endpoints[msg_type] = _Endpoint(handler=handler, kind=kind)
        return self

    def register_all(
        self,
        handlers: Iterable[MessageHandler[Any] | StreamMessageHandler[Any]],
    ) -> "MessageRouter":
        for handler in handlers:
            self.register(handler)
        return self

    def use(self, middleware: Middleware) -> None:
        """Register middleware; the first registered middleware is outermost."""
        self._middleware.append(middleware)

    def has(self, msg_type: str) -> bool:
        return msg_type in self._endpoints

    def get(
        self,
        msg_type: str,
    ) -> MessageHandler[Any] | StreamMessageHandler[Any] | None:
        endpoint = self._endpoints.get(msg_type)
        return endpoint.handler if endpoint is not None else None

    def handlers(
        self,
    ) -> tuple[MessageHandler[Any] | StreamMessageHandler[Any], ...]:
        return tuple(endpoint.handler for endpoint in self._endpoints.values())

    def kinds(self) -> dict[str, str]:
        return {
            msg_type: endpoint.kind for msg_type, endpoint in self._endpoints.items()
        }

    async def dispatch(self, env: Envelope[Any], rctx: Any) -> DispatchResult:
        endpoint = self._endpoints.get(env.type)
        if endpoint is None:
            return UnaryResult(
                response=self._error_response(
                    env,
                    NotFoundError(f"no handler registered for type {env.type!r}"),
                )
            )

        try:
            self._validate_request(endpoint.handler.spec, env)
            core = self._build_core(endpoint)
            chain = self._compose(self._middleware, core)
            return await chain(rctx, env)
        except FrameworkError as exc:
            return UnaryResult(response=self._error_response(env, exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "dispatch failed: type=%s request_id=%s",
                env.type,
                env.metadata.request_id,
            )
            return UnaryResult(
                response=self._error_response(env, FrameworkError(str(exc)))
            )

    @staticmethod
    def _validate_request(spec: HandlerSpec, env: Envelope[Any]) -> None:
        model = spec.request_model
        if model is None:
            return
        try:
            env.rawdata = model.model_validate(env.rawdata)
        except PydanticValidationError as exc:
            raise ValidationError(
                f"invalid rawdata for type {spec.msg_type!r}: {exc}"
            ) from exc

    def _build_core(self, endpoint: _Endpoint):
        if endpoint.kind == _UNARY:
            return self._unary_core(endpoint.handler)
        return self._stream_core(endpoint.handler)

    def _unary_core(self, handler: MessageHandler[Any]):
        async def core(ctx, env):
            result = await self._await_with_lifecycle(
                lambda: handler.handle(ctx, env),
                ctx,
            )
            return UnaryResult(
                response=self._normalize_unary(result, env, handler.spec)
            )

        return core

    def _stream_core(self, handler: StreamMessageHandler[Any]):
        async def core(ctx, env):
            self._check_interrupted(ctx)
            iterator = handler.handle_stream(ctx, env)
            return StreamResult(
                chunks=self._wrap_stream(iterator, env, ctx),
                context=ctx,
            )

        return core

    @staticmethod
    def _compose(middlewares: list[Middleware], core):
        fn = core
        for middleware in reversed(middlewares):
            fn = MessageRouter._wrap_one(middleware, fn)
        return fn

    @staticmethod
    def _wrap_one(middleware, nxt):
        async def wrapped(ctx, env):
            return await middleware(ctx, env, nxt)

        return wrapped

    @staticmethod
    def _normalize_unary(
        result: Any,
        env: Envelope[Any],
        spec: HandlerSpec,
    ) -> ResponseEnvelope:
        if isinstance(result, ResponseEnvelope):
            result.rawdata = MessageRouter._validate_response(spec, result.rawdata)
            return result
        if isinstance(result, dict):
            return ResponseEnvelope(
                type=env.type,
                metadata=env.metadata,
                rawdata=MessageRouter._validate_response(spec, result),
                ok=True,
            )
        raise FrameworkError(
            "unary handler must return dict or ResponseEnvelope, "
            f"got {type(result).__name__}"
        )

    @staticmethod
    def _validate_response(spec: HandlerSpec, rawdata: dict) -> dict:
        model = spec.response_model
        if model is None:
            return rawdata
        try:
            value = model.model_validate(rawdata)
        except Exception as exc:
            raise FrameworkError(
                f"invalid handler response for type {spec.msg_type!r}: {exc}"
            ) from exc
        return dict(value.model_dump(mode="python"))

    def _wrap_stream(
        self,
        iterator: AsyncIterator,
        env: Envelope[Any],
        ctx: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Assign sequence/final flags and preserve request lifecycle handling."""

        async def gen():
            sequence = 0
            stream = iterator.__aiter__()
            try:
                try:
                    next_item = await self._await_with_lifecycle(stream.__anext__, ctx)
                except StopAsyncIteration:
                    return
                while True:
                    current = next_item
                    try:
                        next_item = await self._await_with_lifecycle(
                            stream.__anext__,
                            ctx,
                        )
                    except StopAsyncIteration:
                        sequence += 1
                        yield self._to_chunk(
                            current,
                            sequence,
                            is_final=True,
                            env=env,
                        )
                        return
                    except FrameworkError as exc:
                        sequence += 1
                        yield self._to_chunk(
                            current,
                            sequence,
                            is_final=False,
                            env=env,
                        )
                        sequence += 1
                        yield self._error_chunk(sequence, env, exc)
                        return
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("stream handler failed: type=%s", env.type)
                        sequence += 1
                        yield self._to_chunk(
                            current,
                            sequence,
                            is_final=False,
                            env=env,
                        )
                        sequence += 1
                        yield self._error_chunk(
                            sequence,
                            env,
                            FrameworkError(str(exc)),
                        )
                        return
                    sequence += 1
                    yield self._to_chunk(
                        current,
                        sequence,
                        is_final=False,
                        env=env,
                    )
            except FrameworkError as exc:
                sequence += 1
                yield self._error_chunk(sequence, env, exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("stream handler failed: type=%s", env.type)
                sequence += 1
                yield self._error_chunk(sequence, env, FrameworkError(str(exc)))

        return gen()

    async def _await_with_lifecycle(self, awaitable_factory, ctx: Any):
        self._check_interrupted(ctx)
        remaining = self._remaining_seconds(ctx)
        if remaining is not None and remaining <= 0:
            raise DeadlineExceeded("request deadline exceeded")
        awaitable = awaitable_factory()
        if remaining is None:
            return await awaitable
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                return await awaitable
        except TimeoutError as exc:
            if timeout.expired():
                raise DeadlineExceeded("request deadline exceeded") from exc
            raise

    @staticmethod
    def _check_interrupted(ctx: Any) -> None:
        check = getattr(ctx, "check_interrupted", None)
        if callable(check):
            check()

    @staticmethod
    def _remaining_seconds(ctx: Any) -> float | None:
        remaining = getattr(ctx, "remaining_seconds", None)
        if not callable(remaining):
            return None
        return remaining()

    @staticmethod
    def _to_chunk(
        item: Any,
        sequence: int,
        is_final: bool,
        env: Envelope[Any],
    ) -> StreamChunk:
        if isinstance(item, StreamChunk):
            return StreamChunk(
                sequence=sequence,
                is_final=is_final,
                metadata=env.metadata,
                rawdata=item.rawdata,
                error_code=item.error_code,
                error_message=item.error_message,
            )
        if isinstance(item, dict):
            return StreamChunk(
                sequence=sequence,
                is_final=is_final,
                metadata=env.metadata,
                rawdata=item,
            )
        raise FrameworkError(
            f"stream handler must yield dict or StreamChunk, got {type(item).__name__}"
        )

    @staticmethod
    def _error_response(
        env: Envelope[Any],
        exc: FrameworkError,
    ) -> ResponseEnvelope:
        return ResponseEnvelope(
            type=env.type,
            metadata=env.metadata,
            rawdata={},
            ok=False,
            error_code=exception_code(exc),
            error_message=exc.message,
        )

    @staticmethod
    def _error_chunk(
        sequence: int,
        env: Envelope[Any],
        exc: FrameworkError,
    ) -> StreamChunk:
        return StreamChunk(
            sequence=sequence,
            is_final=True,
            metadata=env.metadata,
            rawdata={},
            error_code=exception_code(exc),
            error_message=exc.message,
        )
