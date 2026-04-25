# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""双 asyncio.Queue：系统（内部）消息优先于用户消息，均为可配置 maxsize。"""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

from openjiuwen_runtime.foundation.log import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class PriorityDualAsyncQueues(Generic[T]):
    """先 drain 系统队列，再等待双队列；系统消息始终优先。"""

    def __init__(self, system_maxsize: int, user_maxsize: int) -> None:
        self._sys: asyncio.Queue[T] = asyncio.Queue(maxsize=system_maxsize)
        self._user: asyncio.Queue[T] = asyncio.Queue(maxsize=user_maxsize)
        self._closed = False
        logger.debug(
            "双队列已创建: system_maxsize=%s user_maxsize=%s", system_maxsize, user_maxsize
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def mark_closed(self) -> None:
        self._closed = True
        logger.info("双队列已标记关闭, 不再接受入队")

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
            logger.error("双队列已关闭, 拒绝 put_user")
            raise RuntimeError("PriorityDualAsyncQueues is closed")
        await self._user.put(item)
        logger.debug("用户队列入队, 当前~长度=%s", self._user.qsize())

    async def put_system(self, item: T) -> None:
        if self._closed:
            logger.error("双队列已关闭, 拒绝 put_system")
            raise RuntimeError("PriorityDualAsyncQueues is closed")
        await self._sys.put(item)
        logger.debug("系统队列入队, 当前~长度=%s", self._sys.qsize())

    async def get(self) -> T:
        """阻塞获取下一条消息；系统队列有数据时先返回系统侧。"""
        if self._closed and self._sys.empty() and self._user.empty():
            raise RuntimeError("PriorityDualAsyncQueues is closed and empty")
        while True:
            if self._closed and self._sys.empty() and self._user.empty():
                raise RuntimeError("PriorityDualAsyncQueues is closed and empty")
            while True:
                try:
                    item = self._sys.get_nowait()
                    logger.debug("双队列 get: 取系统项, sys~=%s user~=%s", self._sys.qsize(), self._user.qsize())
                    return item
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
                    out = t_sys.result()
                    logger.debug("双队列 get: 阻塞取系统项, user~=%s", self._user.qsize())
                else:
                    out = t_usr.result()
                    logger.debug("双队列 get: 阻塞取用户项, sys~=%s", self._sys.qsize())
                return out
            except Exception:
                for p in (t_sys, t_usr):
                    if not p.done():
                        p.cancel()
                logger.error("双队列 get 等待异常", exc_info=True)
                raise
