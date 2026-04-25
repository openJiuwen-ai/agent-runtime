# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""双 asyncio.Queue：系统（内部）消息优先于用户消息，均为可配置 maxsize。"""

from __future__ import annotations

import asyncio
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class PriorityDualAsyncQueues(Generic[T]):
    """先 drain 系统队列，再等待双队列；系统消息始终优先。"""

    def __init__(self, system_maxsize: int, user_maxsize: int) -> None:
        self._sys: asyncio.Queue[T] = asyncio.Queue(maxsize=system_maxsize)
        self._user: asyncio.Queue[T] = asyncio.Queue(maxsize=user_maxsize)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def mark_closed(self) -> None:
        self._closed = True

    @property
    def system_maxsize(self) -> int:
        return self._sys.maxsize  # type: ignore[no-any-return, attr-defined]

    @property
    def user_maxsize(self) -> int:
        return self._user.maxsize  # type: ignore[no-any-return, attr-defined]

    def system_qsize(self) -> int:
        return self._sys.qsize()

    def user_qsize(self) -> int:
        return self._user.qsize()

    async def put_user(self, item: T) -> None:
        if self._closed:
            raise RuntimeError("PriorityDualAsyncQueues is closed")
        await self._user.put(item)

    async def put_system(self, item: T) -> None:
        if self._closed:
            raise RuntimeError("PriorityDualAsyncQueues is closed")
        await self._sys.put(item)

    async def get(self) -> T:
        """阻塞获取下一条消息；系统队列有数据时先返回系统侧。"""
        if self._closed and self._sys.empty() and self._user.empty():
            raise RuntimeError("PriorityDualAsyncQueues is closed and empty")
        while True:
            if self._closed and self._sys.empty() and self._user.empty():
                raise RuntimeError("PriorityDualAsyncQueues is closed and empty")
            while True:
                try:
                    return self._sys.get_nowait()
                except asyncio.QueueEmpty:
                    break
            t_sys = asyncio.create_task(self._sys.get())
            t_usr = asyncio.create_task(self._user.get())
            try:
                done, pending = await asyncio.wait(
                    {t_sys, t_usr},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                assert len(done) == 1
                d = next(iter(done))
                for p in pending:
                    p.cancel()
                    try:
                        await p
                    except (asyncio.CancelledError, Exception):
                        pass
                if d is t_sys:
                    return t_sys.result()
                return t_usr.result()
            except Exception:
                for p in (t_sys, t_usr):
                    if not p.done():
                        p.cancel()
                raise
