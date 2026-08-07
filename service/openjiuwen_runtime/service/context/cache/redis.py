# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Redis-backed shared cache."""

from __future__ import annotations

import math
from typing import Any

from .base import BaseCacheBackend


class RedisCacheBackend(BaseCacheBackend):
    """Cross-replica cache using namespaced Redis keys and millisecond TTLs."""

    def __init__(
        self,
        redis: Any,
        *,
        prefix: str = "service:cache",
        default_ttl: float | None = 300,
        max_value_bytes: int = 1024 * 1024,
        owns_redis: bool = False,
        scan_count: int = 100,
    ) -> None:
        if not prefix or not prefix.rstrip(":"):
            raise ValueError("Redis cache prefix must not be empty")
        super().__init__(
            prefix=prefix,
            default_ttl=default_ttl,
            max_value_bytes=max_value_bytes,
        )
        if isinstance(scan_count, bool) or int(scan_count) <= 0:
            raise ValueError("scan_count must be a positive integer")
        self._redis = redis
        self._owns_redis = owns_redis
        self.scan_count = int(scan_count)

    async def _get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return bytes(value).decode("utf-8")

    async def _set(self, key: str, value: str, ttl: float | None) -> None:
        if ttl is None:
            await self._redis.set(key, value)
            return
        ttl_ms = max(1, math.ceil(ttl * 1000))
        await self._redis.set(key, value, px=ttl_ms)

    async def _delete(self, key: str) -> bool:
        return bool(await self._redis.delete(key))

    async def _exists(self, key: str) -> bool:
        return bool(await self._redis.exists(key))

    async def _clear_namespace(self) -> int:
        cursor: int | bytes = 0
        keys_to_delete: list[Any] = []
        pattern = f"{self.prefix}:*"
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor,
                match=pattern,
                count=self.scan_count,
            )
            keys_to_delete.extend(keys)
            if int(cursor) == 0:
                break
        deleted = 0
        for offset in range(0, len(keys_to_delete), self.scan_count):
            end = offset + self.scan_count
            batch = keys_to_delete[offset:end]
            deleted += int(await self._redis.delete(*batch))
        return deleted

    async def _close(self) -> None:
        if self._owns_redis:
            await self._redis.aclose()

    async def ping(self) -> bool:
        self._ensure_open()
        try:
            return bool(await self._redis.ping())
        except Exception as exc:  # noqa: BLE001 - normalize Redis failures
            self._raise_unavailable("ping", exc)


__all__ = ["RedisCacheBackend"]
