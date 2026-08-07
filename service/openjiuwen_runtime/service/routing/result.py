# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""派发结果（设计 §6.3）。

``dispatch`` 返回 ``DispatchResult``：非流式 → ``UnaryResult``（单个 ResponseEnvelope）；
流式 → ``StreamResult``（StreamChunk 异步迭代器）。适配器据此选择响应方式。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Union

from ..envelope import ResponseEnvelope, StreamChunk


@dataclass
class UnaryResult:
    """非流式派发结果。"""

    response: ResponseEnvelope


class ContextBoundStream:
    """Async iterator that closes its request context at every terminal path."""

    def __init__(self, chunks: AsyncIterator[StreamChunk], context: Any) -> None:
        self._chunks = chunks.__aiter__()
        self._context = context
        self._close_task: asyncio.Task[None] | None = None

    def __aiter__(self) -> "ContextBoundStream":
        return self

    async def __anext__(self) -> StreamChunk:
        if self._close_task is not None:
            raise StopAsyncIteration
        try:
            chunk = await self._chunks.__anext__()
        except StopAsyncIteration:
            await self.aclose()
            raise
        except BaseException:
            await self.aclose()
            raise
        if chunk.is_final:
            await self.aclose()
        return chunk

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._finish())
        task = self._close_task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    async def _finish(self) -> None:
        try:
            close_chunks = getattr(self._chunks, "aclose", None)
            if callable(close_chunks):
                await close_chunks()
        finally:
            close_context = getattr(self._context, "close", None)
            if callable(close_context):
                await close_context()


@dataclass
class StreamResult:
    """流式派发结果，绑定创建该流的请求上下文。"""

    chunks: AsyncIterator[StreamChunk]
    context: Any = None

    def __post_init__(self) -> None:
        if self.context is not None and not isinstance(self.chunks, ContextBoundStream):
            self.chunks = ContextBoundStream(self.chunks, self.context)

    async def aclose(self) -> None:
        close_chunks = getattr(self.chunks, "aclose", None)
        if callable(close_chunks):
            await close_chunks()
        elif self.context is not None:
            close_context = getattr(self.context, "close", None)
            if callable(close_context):
                await close_context()


DispatchResult = Union[UnaryResult, StreamResult]
