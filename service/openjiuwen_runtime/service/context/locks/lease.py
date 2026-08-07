# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""请求范围内的锁租约和自动续约任务。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from enum import Enum
from typing import Any, Callable

from ...errors import InvalidLockLease, LockLost
from .base import LockBackend, LockCredential

_logger = logging.getLogger("openjiuwen_runtime.service.locks")


class LeaseState(str, Enum):
    NEW = "NEW"
    HELD = "HELD"
    LOST = "LOST"
    RELEASED = "RELEASED"


Callback = Callable[..., Any]


class LockLease:
    """绑定单次凭证的锁租约。

    后端只执行一次原子操作，续约循环、状态转换和请求中断由本类统一管理。
    """

    def __init__(
        self,
        backend: LockBackend,
        credential: LockCredential,
        *,
        ttl: float,
        auto_renew: bool = True,
        renew_ratio: float = 1 / 3,
        check_interrupted: Callable[[], None] | None = None,
        on_lost: Callback | None = None,
        on_release: Callback | None = None,
        release_timeout: float = 3.0,
    ) -> None:
        self.backend = backend
        self._credential = credential
        self._ttl = ttl
        self._auto_renew = auto_renew
        self._renew_ratio = renew_ratio
        self._check_interrupted = check_interrupted
        self._on_lost = on_lost
        self._on_release = on_release
        self._release_timeout = release_timeout
        self._state = LeaseState.HELD
        self.lost_event = asyncio.Event()
        self._operation_lock = asyncio.Lock()
        self._renew_task: asyncio.Task[None] | None = None
        self._release_task: asyncio.Task[None] | None = None
        self._lost_reason: str | None = None
        self._ever_lost = False
        if auto_renew:
            self._renew_task = asyncio.create_task(
                self._renew_loop(), name=f"lock-renew:{credential.key}"
            )

    @property
    def credential(self) -> LockCredential:
        return self._credential

    @property
    def key(self) -> str:
        return self._credential.key

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def lost(self) -> bool:
        return self._state is LeaseState.LOST

    @property
    def released(self) -> bool:
        return self._state is LeaseState.RELEASED

    async def __aenter__(self) -> "LockLease":
        self.ensure_valid()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await self.release()
        if self._ever_lost and exc_type is None:
            raise LockLost(self._lost_reason or f"lock {self.key!r} was lost")
        return False

    def ensure_valid(self) -> None:
        """在业务关键边界检查本地租约状态。"""
        if self._state is LeaseState.LOST:
            raise InvalidLockLease(self._lost_reason or f"lock {self.key!r} was lost")
        if self._state is LeaseState.RELEASED:
            raise InvalidLockLease(f"lock {self.key!r} has been released")
        if self._state is not LeaseState.HELD:
            raise InvalidLockLease(f"lock {self.key!r} is not held")
        if self._credential.expires_at <= time.monotonic():
            self._mark_lost(f"lock {self.key!r} lease expired")
            raise InvalidLockLease(f"lock {self.key!r} lease expired")

    async def renew(self) -> LockCredential:
        """续约并替换当前凭证。"""
        async with self._operation_lock:
            self.ensure_valid()
            if self._check_interrupted is not None:
                self._check_interrupted()
            try:
                credential = await self.backend.renew(self._credential, self._ttl)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - backend failures lose the lease
                self._mark_lost(f"lock {self.key!r} renewal failed: {exc}")
                if isinstance(exc, LockLost):
                    raise
                raise LockLost(f"lock {self.key!r} renewal failed") from exc
            self._credential = credential
            return credential

    async def release(self, *, timeout: float | None = None) -> bool:
        """释放租约；重复调用安全且不会重新删除后继持有者的锁。"""
        if self._release_task is None:
            self._release_task = asyncio.create_task(
                self._release_impl(), name=f"lock-release:{self.key}"
            )
        task = self._release_task
        limit = self._release_timeout if timeout is None else timeout
        try:
            if limit is not None and limit > 0:
                return bool(await asyncio.wait_for(asyncio.shield(task), timeout=limit))
            return bool(await asyncio.shield(task))
        except asyncio.CancelledError:
            # 请求取消不能中断已经开始的释放操作。
            try:
                await asyncio.shield(task)
            except BaseException:  # noqa: BLE001 - preserve the cancellation
                pass
            raise
        except asyncio.TimeoutError:
            # 后端调用继续在后台完成，避免超时路径留下半释放状态。
            return False

    async def _release_impl(self) -> bool:
        async with self._operation_lock:
            if self._state is LeaseState.RELEASED:
                return True
            if (
                self._renew_task is not None
                and self._renew_task is not asyncio.current_task()
            ):
                self._renew_task.cancel()
                try:
                    await self._renew_task
                except BaseException:  # noqa: BLE001 - release remains best effort
                    pass
            released = False
            try:
                released = bool(await self.backend.release(self._credential))
            except BaseException:  # noqa: BLE001 - cleanup must be idempotent
                _logger.exception("lock release failed: key=%s", self.key)
            self._state = LeaseState.RELEASED
            callback = self._on_release
            if callback is not None:
                try:
                    result = callback(self)
                    if inspect.isawaitable(result):
                        await result
                except BaseException:  # noqa: BLE001
                    _logger.exception("lock release callback failed: key=%s", self.key)
            return released

    async def wait_lost(self) -> None:
        await self.lost_event.wait()

    def _mark_lost(self, reason: str) -> None:
        if self._state in (LeaseState.LOST, LeaseState.RELEASED):
            return
        self._state = LeaseState.LOST
        self._ever_lost = True
        self._lost_reason = reason
        self.lost_event.set()
        callback = self._on_lost
        if callback is not None:
            try:
                result = callback(reason)
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except BaseException:  # noqa: BLE001
                _logger.exception("lock lost callback failed: key=%s", self.key)

    async def _renew_loop(self) -> None:
        interval = max(0.01, self._ttl * self._renew_ratio)
        try:
            while self._state is LeaseState.HELD:
                jitter = random.uniform(0.0, interval * 0.1)
                await asyncio.sleep(interval + jitter)
                if self._state is not LeaseState.HELD:
                    return
                try:
                    await self.renew()
                except asyncio.CancelledError:
                    return
                except BaseException as exc:  # noqa: BLE001
                    self._mark_lost(str(exc) or f"lock {self.key!r} renewal failed")
                    return
        except asyncio.CancelledError:
            return


__all__ = ["LeaseState", "LockLease"]
