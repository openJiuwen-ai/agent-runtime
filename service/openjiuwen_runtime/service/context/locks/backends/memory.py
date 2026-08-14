# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Process-local lock backend."""

from __future__ import annotations

import asyncio
import heapq
import math
import time
from dataclasses import dataclass
from uuid import uuid4

from ....errors import InvalidLockLease, LockBackendUnavailable, LockLost
from ..base import LockCapabilities, LockCredential


@dataclass(slots=True)
class _Entry:
    token: str
    expires_at: float


class MemoryLockBackend:
    """An in-process, token-checked TTL lock backend.

    Waiting is intentionally absent from this class. ``LockManager`` owns the
    retry policy so all lock backends expose the same behavior.
    """

    capabilities = LockCapabilities(distributed=False, fencing=False)

    def __init__(self, *, prefix: str = "lock") -> None:
        self.prefix = prefix
        self._entries: dict[str, _Entry] = {}
        self._expiry_heap: list[tuple[float, str, str]] = []
        self._lock = asyncio.Lock()
        self._closed = False

    def format_key(self, key: str) -> str:
        return f"{self.prefix}:{key}" if self.prefix else key

    @staticmethod
    def _ttl(ttl: float) -> float:
        ttl = float(ttl)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be a finite positive number")
        return ttl

    async def try_acquire(self, key: str, ttl: float) -> LockCredential | None:
        if self._closed:
            raise LockBackendUnavailable("memory lock backend is closed")
        ttl = self._ttl(ttl)
        full_key = self.format_key(key)
        now = time.monotonic()
        async with self._lock:
            self._purge_expired(now)
            if full_key in self._entries:
                return None
            token = uuid4().hex
            expires_at = now + ttl
            self._entries[full_key] = _Entry(token, expires_at)
            heapq.heappush(self._expiry_heap, (expires_at, full_key, token))
            return LockCredential(
                key=full_key,
                token=token,
                backend="memory",
                lease_id=None,
                fencing_token=None,
                acquired_at=now,
                expires_at=expires_at,
            )

    async def renew(self, credential: LockCredential, ttl: float) -> LockCredential:
        ttl = self._ttl(ttl)
        self._validate_credential(credential)
        now = time.monotonic()
        async with self._lock:
            self._purge_expired(now)
            entry = self._entries.get(credential.key)
            if entry is None or entry.token != credential.token:
                raise LockLost(f"lock {credential.key!r} is no longer owned")
            expires_at = now + ttl
            entry.expires_at = expires_at
            heapq.heappush(
                self._expiry_heap, (expires_at, credential.key, credential.token)
            )
            return credential.renewed(ttl)

    async def release(self, credential: LockCredential) -> bool:
        self._validate_credential(credential)
        async with self._lock:
            self._purge_expired(time.monotonic())
            entry = self._entries.get(credential.key)
            if entry is None or entry.token != credential.token:
                return False
            del self._entries[credential.key]
            return True

    async def ping(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._entries.clear()
            self._expiry_heap.clear()

    def _purge_expired(self, now: float) -> None:
        while self._expiry_heap and self._expiry_heap[0][0] <= now:
            expires_at, key, token = heapq.heappop(self._expiry_heap)
            entry = self._entries.get(key)
            if (
                entry is not None
                and entry.token == token
                and entry.expires_at == expires_at
            ):
                del self._entries[key]

    @staticmethod
    def _validate_credential(credential: LockCredential) -> None:
        if credential.backend != "memory":
            raise InvalidLockLease(
                f"credential backend {credential.backend!r} cannot be used with memory"
            )


__all__ = ["MemoryLockBackend"]
