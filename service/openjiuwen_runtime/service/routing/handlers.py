# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Handler 与中间件协议 / 类型别名（设计 §6.2、§6.3）。

handler 与传输无关：签名只认 ``(ctx, env)``，没有 Request/WebSocket。
ctx 为 RequestContext（duck-typed，本模块仅作注解，运行期不强依赖）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Protocol, TypeVar, Union, runtime_checkable

from ..envelope import Envelope, ResponseEnvelope, StreamChunk

if TYPE_CHECKING:  # 仅注解用，避免运行期循环导入
    from ..context.request_context import RequestContext

TRequest = TypeVar("TRequest")

# handler 返回：dict（框架包成 ResponseEnvelope）或现成的 ResponseEnvelope
UnaryReturn = Union[ResponseEnvelope, dict]
Handler = Callable[[Any, Envelope[Any]], Awaitable[UnaryReturn]]
StreamHandler = Callable[[Any, Envelope[Any]], AsyncIterator[StreamChunk]]

# 中间件：洋葱模型，nxt 为链中下一步
Middleware = Callable[[Any, Envelope[Any], "Next"], Awaitable[Any]]
Next = Callable[[Any, Envelope[Any]], Awaitable[Any]]


@runtime_checkable
class MessageHandler(Protocol):
    """非流式 handler 协议：``async (ctx, env) -> dict | ResponseEnvelope``。"""

    async def __call__(
        self,
        ctx: "RequestContext[TRequest]",
        env: Envelope[TRequest],
    ) -> UnaryReturn:  # pragma: no cover - 协议
        ...


@runtime_checkable
class StreamMessageHandler(Protocol):
    """流式 handler 协议：``(ctx, env) -> AsyncIterator[StreamChunk]``。"""

    def __call__(
        self,
        ctx: "RequestContext[TRequest]",
        env: Envelope[TRequest],
    ) -> AsyncIterator[StreamChunk]:  # pragma: no cover - 协议
        ...
