# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""双 asyncio.Queue：系统（内部）消息优先于用户消息，均为可配置 maxsize。"""

from __future__ import annotations

import asyncio
from collections import deque
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
        # 竞态救援暂存：get() 阻塞期间系统/用户两侧在同一事件循环窗口入队时，
        # 两个 getter 均被唤醒取走消息；系统项当次交付，用户项暂存于此，
        # 下次 get() 最先交付（先于新消息，保证用户侧 FIFO 不乱序、不丢失）。
        self._rescued_user: deque[T] = deque()
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
        """用户侧待交付数 = 用户队列长度 + 竞态救援暂存数。"""
        return self._user.qsize() + len(self._rescued_user)

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
        """阻塞获取下一条消息；系统队列有数据时先返回系统侧。

        竞态说明：阻塞等待依赖同时挂两个 getter；两侧在同一事件循环窗口内先后入队时，
        ``asyncio.wait(FIRST_COMPLETED)`` 恢复时可能两个 getter 均已 done（len(done)==2）。
        修复前该场景被误判为致命错误抛出，两条消息被 getter 取走后直接丢失，且消费方
        （ServiceManager._message_loop）会因 RuntimeError 永久退出。修复后：系统项当次
        交付，用户项转入 ``_rescued_user`` 暂存，下次 get() 最先交付。
        """
        # 救援暂存优先交付（保持用户侧 FIFO，不因竞态乱序/丢失）
        if self._rescued_user:
            item = self._rescued_user.popleft()
            logger.debug(
                "双队列 get: 交付暂存用户项, 剩余暂存=%s", len(self._rescued_user)
            )
            return item
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
                if len(done) == 2:
                    # 两侧同窗口就绪：交付系统项，用户项暂存，下次 get() 最先交付
                    self._rescued_user.append(t_usr.result())
                    logger.info(
                        "双队列 get: 系统/用户侧同窗口就绪, 用户项已暂存待下次交付: "
                        "stash=%s (修复前此场景会丢失两条消息)",
                        len(self._rescued_user),
                    )
                    return t_sys.result()
                if not done:
                    # FIRST_COMPLETED 正常返回时 done 必非空，此处仅防御性兜底
                    raise RuntimeError("双队列 get 异常: 无就绪 getter (done=0)")
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
