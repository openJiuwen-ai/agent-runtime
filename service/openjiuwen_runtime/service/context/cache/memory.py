# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Bounded process-local LRU cache with lazy TTL expiration."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass

from .base import BaseCacheBackend


@dataclass(slots=True)
class _Entry:
    value: str
    expires_at: float | None


class MemoryCacheBackend(BaseCacheBackend):
    """A concurrency-safe local cache for reconstructable hot data."""

    def __init__(
        self,
        *,
        prefix: str = "service:cache",
        default_ttl: float | None = 300,
        max_entries: int = 1000,
        max_value_bytes: int = 1024 * 1024,
    ) -> None:
        super().__init__(
            prefix=prefix,
            default_ttl=default_ttl,
            max_value_bytes=max_value_bytes,
        )
        if isinstance(max_entries, bool) or int(max_entries) <= 0:
            raise ValueError("max_entries must be a positive integer")
        self.max_entries = int(max_entries)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def _get(self, key: str) -> str | None:
        async with self._lock:
            self._ensure_open()
            self._purge_expired(time.monotonic())
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry.value

    async def _set(self, key: str, value: str, ttl: float | None) -> None:
        async with self._lock:
            self._ensure_open()
            now = time.monotonic()
            self._purge_expired(now)
            self._entries.pop(key, None)
            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)
                self.metrics.evictions += 1
            expires_at = None if ttl is None else now + ttl
            self._entries[key] = _Entry(value=value, expires_at=expires_at)

    async def _delete(self, key: str) -> bool:
        async with self._lock:
            self._ensure_open()
            self._purge_expired(time.monotonic())
            return self._entries.pop(key, None) is not None

    async def _exists(self, key: str) -> bool:
        async with self._lock:
            self._ensure_open()
            self._purge_expired(time.monotonic())
            return key in self._entries

    async def _clear_namespace(self) -> int:
        async with self._lock:
            self._ensure_open()
            count = len(self._entries)
            self._entries.clear()
            return count

    async def _close(self) -> None:
        async with self._lock:
            self._entries.clear()

    async def ping(self) -> bool:
        self._ensure_open()
        return True

    def _purge_expired(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.expires_at is not None and entry.expires_at <= now
        ]
        for key in expired:
            del self._entries[key]
        self.metrics.expirations += len(expired)


__all__ = ["MemoryCacheBackend"]
