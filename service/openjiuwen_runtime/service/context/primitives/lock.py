# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""分布式锁（设计 §9.1）。

``SET key owner NX PX ttl`` 抢锁；释放与续期用 WATCH/MULTI 做 compare-and-set（仅 owner
匹配才删/续），避免误删别人的锁。后台自动续期；续期失锁 → 退出时抛 ``LockLost``。
``timeout=0`` 非阻塞、抢不到抛 ``LockNotAcquired``。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from redis.exceptions import WatchError

from ...errors import LockLost, LockNotAcquired

logger = logging.getLogger(__name__)

_ACQUIRE_RETRY = 0.05  # 阻塞抢锁时的轮询间隔（秒）


class DistributedLock:
    """一次锁获取：``async with ctx.lock(key, ttl=..., timeout=...)``。"""

    def __init__(
        self,
        redis: Any,
        key: str,
        *,
        owner: str,
        ttl: float = 30,
        timeout: float = 0,
        prefix: str = "lock",
        renew_interval: float | None = None,
        check_interrupted: Callable[[], None] | None = None,
    ) -> None:
        self._redis = redis
        self.key = f"{prefix}:{key}" if prefix else key
        self._owner = owner
        self._ttl_ms = int(ttl * 1000)
        self._timeout = timeout
        self._renew_interval = renew_interval if renew_interval is not None else max(1.0, ttl / 3)
        self._renew_task: asyncio.Task | None = None
        self._lost = False
        self._check_interrupted = check_interrupted

    # -------------------------------------------------------------- 抢锁
    async def _try_acquire(self) -> bool:
        if self._check_interrupted is not None:
            self._check_interrupted()
        ok = await self._redis.set(self.key, self._owner, nx=True, px=self._ttl_ms)
        return bool(ok)

    async def __aenter__(self) -> "DistributedLock":
        if self._timeout and self._timeout > 0:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + self._timeout
            while True:
                if await self._try_acquire():
                    break
                if loop.time() >= deadline:
                    raise LockNotAcquired(
                        f"could not acquire lock {self.key!r} within {self._timeout}s")
                await asyncio.sleep(_ACQUIRE_RETRY)
        else:
            if not await self._try_acquire():
                raise LockNotAcquired(f"could not acquire lock {self.key!r} (non-blocking)")
        self._renew_task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._renew_task is not None:
            self._renew_task.cancel()
            try:
                await self._renew_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        lost = self._lost
        await self._safe_release()
        # 仅在 body 无异常时抛 LockLost，避免掩盖原始异常
        if lost and exc_type is None:
            raise LockLost(f"lock {self.key!r} lost during hold (renew failed)")
        return False

    # -------------------------------------------------------------- 续期
    async def renew_once(self) -> bool:
        """续期一次：仍是 owner 则续 TTL 返回 True，失锁返回 False。"""
        return await self._cas(lambda pipe: pipe.pexpire(self.key, self._ttl_ms))

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._renew_interval)
                if not await self.renew_once():
                    self._lost = True
                    return
        except asyncio.CancelledError:
            return

    # -------------------------------------------------------------- 释放
    async def _safe_release(self) -> bool:
        return await self._cas(lambda pipe: pipe.delete(self.key))

    async def _cas(self, mutate) -> bool:
        """WATCH/MULTI compare-and-set：仅当当前 value == owner 时执行 mutate。"""
        owner_b = self._owner.encode() if isinstance(self._owner, str) else self._owner
        pipe = self._redis.pipeline(transaction=True)
        try:
            await pipe.watch(self.key)
            cur = await pipe.get(self.key)
            if _as_bytes(cur) == owner_b:
                pipe.multi()
                mutate(pipe)
                await pipe.execute()
                return True
            await pipe.unwatch()
            return False
        except WatchError:
            try:
                await pipe.unwatch()
            except Exception:  # noqa: BLE001
                pass
            return False


def _as_bytes(v: Any) -> bytes | None:
    if v is None:
        return None
    return v.encode() if isinstance(v, str) else bytes(v)
