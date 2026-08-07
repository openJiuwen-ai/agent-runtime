# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""锁获取等待、租约登记和请求关闭回收。"""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Callable

from ...errors import (
    DeadlineExceeded,
    Interrupted,
    LockAcquireTimeout,
    LockBackendUnavailable,
    LockNotAcquired,
)
from .base import LockBackend, LockCapabilities, LockCredential
from .lease import LockLease


class LockManager:
    """为一个请求统一管理锁等待、续约和清理。"""

    def __init__(
        self,
        backend: LockBackend | None,
        *,
        cancel_event: asyncio.Event | None = None,
        deadline: float | None = None,
        check_interrupted: Callable[[], None] | None = None,
        interrupt: Callable[[str | None], None] | None = None,
        default_ttl: float = 30.0,
        default_wait_timeout: float = 0.0,
        renew_ratio: float = 1 / 3,
        release_timeout: float = 3.0,
    ) -> None:
        self.backend = backend
        self.cancel_event = cancel_event
        self.deadline = deadline
        self._check_interrupted = check_interrupted
        self._interrupt = interrupt
        self.default_ttl = self._positive_finite("default_ttl", default_ttl)
        self.default_wait_timeout = float(default_wait_timeout)
        if (
            not math.isfinite(self.default_wait_timeout)
            or self.default_wait_timeout < 0
        ):
            raise ValueError(
                "default_wait_timeout must be a finite non-negative number"
            )
        self.renew_ratio = self._positive_finite("renew_ratio", renew_ratio)
        self.release_timeout = self._positive_finite("release_timeout", release_timeout)
        self._leases: set[LockLease] = set()
        self._closed = False

    @staticmethod
    def _positive_finite(name: str, value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite positive number")
        return value

    @property
    def capabilities(self) -> LockCapabilities:
        if self.backend is None:
            return LockCapabilities(distributed=False, fencing=False)
        return self.backend.capabilities

    @property
    def active_leases(self) -> tuple[LockLease, ...]:
        return tuple(self._leases)

    @property
    def closed(self) -> bool:
        return self._closed

    def require(
        self, *, distributed: bool = False, fencing: bool = False
    ) -> "LockManager":
        if self.backend is None:
            raise LockBackendUnavailable("lock backend is not configured")
        capabilities = self.capabilities
        if distributed and not capabilities.distributed:
            raise LockBackendUnavailable("configured lock backend is not distributed")
        if fencing and not capabilities.fencing:
            raise LockBackendUnavailable(
                "configured lock backend does not provide fencing tokens"
            )
        return self

    def backend_key(self, key: str) -> str:
        if self.backend is None:
            return key
        formatter = getattr(self.backend, "format_key", None)
        return formatter(key) if callable(formatter) else key

    async def acquire(
        self,
        key: str,
        *,
        ttl: float | None = None,
        wait_timeout: float | None = None,
        auto_renew: bool = False,
        renew_ratio: float | None = None,
    ) -> LockLease:
        """等待并获取锁，成功后返回已登记的 ``LockLease``。"""
        if self._closed:
            raise LockBackendUnavailable("lock manager is closed")
        self.require()
        if not isinstance(key, str) or not key:
            raise ValueError("lock key must be a non-empty string")
        ttl = self.default_ttl if ttl is None else self._positive_finite("ttl", ttl)
        wait_timeout = (
            self.default_wait_timeout if wait_timeout is None else float(wait_timeout)
        )
        if not math.isfinite(wait_timeout) or wait_timeout < 0:
            raise ValueError("wait_timeout must be a finite non-negative number")
        ratio = (
            self.renew_ratio
            if renew_ratio is None
            else self._positive_finite("renew_ratio", renew_ratio)
        )

        started = time.monotonic()
        wait_deadline = started + wait_timeout if wait_timeout > 0 else started
        attempt = 0
        while True:
            self._check_request_state()
            credential = await self._try_acquire_cancel_safe(key, ttl)
            try:
                self._check_request_state()
            except BaseException:
                if credential is not None:
                    await self._compensate_release(credential)
                raise
            if credential is not None:
                lease = LockLease(
                    self.backend,  # type: ignore[arg-type]
                    credential,
                    ttl=ttl,
                    auto_renew=auto_renew,
                    renew_ratio=ratio,
                    check_interrupted=self._check_request_state,
                    on_lost=self._handle_lost,
                    on_release=self._unregister,
                    release_timeout=self.release_timeout,
                )
                self._leases.add(lease)
                return lease

            now = time.monotonic()
            if wait_timeout == 0:
                raise LockNotAcquired(f"could not acquire lock {key!r} (non-blocking)")
            if now >= wait_deadline:
                raise LockAcquireTimeout(
                    f"could not acquire lock {key!r} within {wait_timeout}s"
                )
            remaining = wait_deadline - now
            request_remaining = self._request_remaining()
            if request_remaining is not None:
                if request_remaining <= 0:
                    self._check_request_state()
                remaining = min(remaining, request_remaining)
            base_delay = min(0.02 * (2 ** min(attempt, 4)), 0.25)
            delay = min(remaining, base_delay + random.uniform(0.0, base_delay * 0.2))
            await self._wait_for_retry(delay)
            attempt += 1

    @asynccontextmanager
    async def hold(
        self,
        key: str,
        *,
        ttl: float | None = None,
        wait_timeout: float | None = None,
        auto_renew: bool = True,
        renew_ratio: float | None = None,
    ) -> AsyncIterator[LockLease]:
        lease = await self.acquire(
            key,
            ttl=ttl,
            wait_timeout=wait_timeout,
            auto_renew=auto_renew,
            renew_ratio=renew_ratio,
        )
        async with lease:
            yield lease

    async def close(self) -> None:
        """停止续约任务并在独立短超时内回收当前请求的全部租约。"""
        if self._closed:
            return
        self._closed = True
        leases = tuple(self._leases)
        if not leases:
            return
        await asyncio.gather(
            *(lease.release(timeout=self.release_timeout) for lease in leases),
            return_exceptions=True,
        )

    def _check_request_state(self) -> None:
        if self._check_interrupted is not None:
            self._check_interrupted()
            return
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise Interrupted("request interrupted")
        if self.deadline is not None and self.deadline <= time.monotonic():
            raise DeadlineExceeded("request deadline exceeded")

    def _request_remaining(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    async def _wait_for_retry(self, delay: float) -> None:
        if delay <= 0:
            self._check_request_state()
            return
        if self.cancel_event is None:
            await asyncio.sleep(delay)
        else:
            try:
                await asyncio.wait_for(self.cancel_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        self._check_request_state()

    async def _try_acquire_cancel_safe(
        self, key: str, ttl: float
    ) -> LockCredential | None:
        backend = self.backend
        if backend is None:  # Guarded by require().
            raise LockBackendUnavailable("lock backend is not configured")
        task = asyncio.create_task(backend.try_acquire(key, ttl))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            credential: LockCredential | None = None
            try:
                credential = await asyncio.shield(task)
            except BaseException:  # noqa: BLE001 - preserve caller cancellation
                pass
            if credential is not None:
                await self._compensate_release(credential)
            raise

    async def _compensate_release(self, credential: LockCredential) -> None:
        backend = self.backend
        if backend is None:
            return
        task = asyncio.create_task(backend.release(credential))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self.release_timeout)
        except BaseException:  # noqa: BLE001 - preserve the acquisition interruption
            pass

    def _handle_lost(self, reason: str) -> None:
        if self._interrupt is not None:
            self._interrupt(reason)

    def _unregister(self, lease: LockLease) -> None:
        self._leases.discard(lease)


__all__ = ["LockManager"]
