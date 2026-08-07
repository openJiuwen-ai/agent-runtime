# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Transport-independent handler contracts and reusable registries.

Applications may use ``@app.handle`` / ``@app.stream`` for small local
handlers, implement :class:`MessageHandler` / :class:`StreamMessageHandler`
for reusable dependency-injected handlers, or collect either form in a
:class:`HandlerRegistry` that can be included by an application.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Generic,
    Iterable,
    Protocol,
    TypeVar,
)

from pydantic import BaseModel

from ..envelope import Envelope, ResponseEnvelope, StreamChunk
from ..errors import ValidationError

if TYPE_CHECKING:
    from ..context.request_context import RequestContext

TRequest = TypeVar("TRequest")

UnaryReturn = ResponseEnvelope | dict
UnaryCallable = Callable[[Any, Envelope[Any]], Awaitable[UnaryReturn]]
StreamCallable = Callable[[Any, Envelope[Any]], AsyncIterator[StreamChunk | dict]]

# Middleware uses an onion chain; ``nxt`` invokes the next middleware or handler.
Middleware = Callable[[Any, Envelope[Any], "Next"], Awaitable[Any]]
Next = Callable[[Any, Envelope[Any]], Awaitable[Any]]


@dataclass(frozen=True)
class HandlerSpec:
    """Transport-independent metadata for one registered message type."""

    msg_type: str
    request_model: type[BaseModel] | None = None
    response_model: type[BaseModel] | None = None
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        normalized_type = str(self.msg_type or "").strip()
        if not normalized_type:
            raise ValidationError("msg_type must be a non-empty string")
        object.__setattr__(self, "msg_type", normalized_type)


class MessageHandler(ABC, Generic[TRequest]):
    """Object-oriented contract for a non-streaming handler."""

    @property
    @abstractmethod
    def spec(self) -> HandlerSpec:
        """Describe the route, request model, and optional response model."""
        raise NotImplementedError

    @abstractmethod
    async def handle(
        self,
        ctx: "RequestContext[TRequest]",
        env: Envelope[TRequest],
    ) -> UnaryReturn:
        """Handle one request and return a dict or ResponseEnvelope."""
        raise NotImplementedError


class StreamMessageHandler(ABC, Generic[TRequest]):
    """Object-oriented contract for an async-generator handler."""

    @property
    @abstractmethod
    def spec(self) -> HandlerSpec:
        """Describe the route and request model."""
        raise NotImplementedError

    @abstractmethod
    async def handle_stream(
        self,
        ctx: "RequestContext[TRequest]",
        env: Envelope[TRequest],
    ) -> AsyncIterator[StreamChunk | dict]:
        """Yield response chunks asynchronously."""
        if False:  # pragma: no cover - keep the abstract contract async-iterable
            yield {}
        raise NotImplementedError


class FunctionMessageHandler(MessageHandler[Any]):
    """Adapt an ``async def`` function to :class:`MessageHandler`."""

    def __init__(self, spec: HandlerSpec, handler: UnaryCallable) -> None:
        _require_async_function(handler, role=f"handler for {spec.msg_type}")
        self._spec = spec
        self._handler = handler

    @property
    def spec(self) -> HandlerSpec:
        return self._spec

    async def handle(self, ctx: "RequestContext[Any]", env: Envelope[Any]):
        return await self._handler(ctx, env)


class FunctionStreamMessageHandler(StreamMessageHandler[Any]):
    """Adapt an async-generator function to :class:`StreamMessageHandler`."""

    def __init__(self, spec: HandlerSpec, handler: StreamCallable) -> None:
        _require_async_generator_function(
            handler,
            role=f"stream handler for {spec.msg_type}",
        )
        self._spec = spec
        self._handler = handler

    @property
    def spec(self) -> HandlerSpec:
        return self._spec

    async def handle_stream(
        self,
        ctx: "RequestContext[Any]",
        env: Envelope[Any],
    ) -> AsyncIterator[StreamChunk | dict]:
        async for item in self._handler(ctx, env):
            yield item


class HandlerModule(Protocol):
    """A module that contributes a collection of handlers to an App."""

    def handlers(self) -> Iterable[MessageHandler[Any] | StreamMessageHandler[Any]]:
        raise NotImplementedError


class HandlerRegistry:
    """Collect reusable handlers before including them in an application."""

    def __init__(self) -> None:
        self._handlers: dict[
            str,
            MessageHandler[Any] | StreamMessageHandler[Any],
        ] = {}

    def register(
        self,
        handler: MessageHandler[Any] | StreamMessageHandler[Any],
    ) -> "HandlerRegistry":
        _validate_handler(handler)
        msg_type = handler.spec.msg_type
        if msg_type in self._handlers:
            raise ValueError(f"handler already registered for type {msg_type!r}")
        self._handlers[msg_type] = handler
        return self

    def register_all(
        self,
        handlers: Iterable[MessageHandler[Any] | StreamMessageHandler[Any]],
    ) -> "HandlerRegistry":
        for handler in handlers:
            self.register(handler)
        return self

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
        """Register an async non-streaming function in this registry."""
        spec = _make_spec(
            msg_type,
            request_model=request_model,
            response_model=response_model,
            summary=summary,
            description=description,
            tags=tags,
        )

        def decorator(fn: UnaryCallable) -> UnaryCallable:
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
        """Register an async-generator function in this registry."""
        spec = _make_spec(
            msg_type,
            request_model=request_model,
            summary=summary,
            description=description,
            tags=tags,
        )

        def decorator(fn: StreamCallable) -> StreamCallable:
            self.register(FunctionStreamMessageHandler(spec, fn))
            return fn

        return decorator

    def handlers(
        self,
    ) -> tuple[MessageHandler[Any] | StreamMessageHandler[Any], ...]:
        return tuple(self._handlers.values())


def _make_spec(
    msg_type: str,
    *,
    request_model: type[BaseModel] | None = None,
    response_model: type[BaseModel] | None = None,
    summary: str | None = None,
    description: str | None = None,
    tags: Iterable[str] | None = None,
) -> HandlerSpec:
    return HandlerSpec(
        msg_type=str(msg_type).strip(),
        request_model=request_model,
        response_model=response_model,
        summary=summary,
        description=description,
        tags=tuple(tags or ()),
    )


def _validate_handler(handler: Any) -> None:
    spec = getattr(handler, "spec", None)
    if not isinstance(spec, HandlerSpec):
        raise TypeError("handler.spec must be a HandlerSpec")
    for name, model in (
        ("request_model", spec.request_model),
        ("response_model", spec.response_model),
    ):
        if model is not None and not (
            isinstance(model, type) and issubclass(model, BaseModel)
        ):
            raise TypeError(f"handler {name} must be a Pydantic model")
    if isinstance(handler, MessageHandler):
        _require_async_function(
            handler.handle,
            role=f"handler for {handler.spec.msg_type}",
        )
        return
    if isinstance(handler, StreamMessageHandler):
        _require_async_generator_function(
            handler.handle_stream,
            role=f"stream handler for {handler.spec.msg_type}",
        )
        return
    raise TypeError("handler must implement MessageHandler or StreamMessageHandler")


def _require_async_function(callback: Any, *, role: str) -> None:
    if not callable(callback) or not inspect.iscoroutinefunction(callback):
        raise TypeError(f"{role} must be declared with async def")


def _require_async_generator_function(callback: Any, *, role: str) -> None:
    if not callable(callback) or not inspect.isasyncgenfunction(callback):
        raise TypeError(f"{role} must be declared as an async generator function")


__all__ = [
    "FunctionMessageHandler",
    "FunctionStreamMessageHandler",
    "HandlerModule",
    "HandlerRegistry",
    "HandlerSpec",
    "MessageHandler",
    "Middleware",
    "Next",
    "StreamMessageHandler",
    "UnaryReturn",
]
