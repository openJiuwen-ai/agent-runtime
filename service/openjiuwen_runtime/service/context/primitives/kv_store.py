# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""分布式字典 / 会话存储（设计 §9.3）。

Redis kv + TTL，按前缀命名空间；之上 set_json/get_json 做结构化存取。
跨副本共享的"黑板"——顶替进程内 dict，满足无内存状态硬约束。
"""
from __future__ import annotations

import json
from typing import Any


class KVStore:
    def __init__(self, redis: Any, prefix: str = "kv") -> None:
        self._redis = redis
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}" if self._prefix else key

    async def get(self, key: str) -> str | None:
        val = await self._redis.get(self._key(key))
        if val is None:
            return None
        return val.decode() if isinstance(val, (bytes, bytearray)) else val

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        k = self._key(key)
        if ttl is not None:
            await self._redis.set(k, value, ex=ttl)
        else:
            await self._redis.set(k, value)

    async def delete(self, key: str) -> bool:
        return bool(await self._redis.delete(self._key(key)))

    async def exists(self, key: str) -> bool:
        return bool(await self._redis.exists(self._key(key)))

    async def incr(self, key: str, amount: int = 1) -> int:
        """原子递增（Redis INCRBY）；跨副本全局递增。"""
        return int(await self._redis.incrby(self._key(key), amount))

    async def set_json(self, key: str, obj: Any, ttl: int | None = None) -> None:
        await self.set(key, json.dumps(obj), ttl=ttl)

    async def get_json(self, key: str, default: Any = None) -> Any:
        raw = await self.get(key)
        if raw is None:
            return default
        return json.loads(raw)

    async def scan(self, pattern: str) -> list[str]:
        """按用户空间 pattern 扫描键（剥离前缀后返回）。"""
        full = self._key(pattern)
        keys: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await self._redis.scan(cursor=cursor, match=full, count=100)
            for k in batch:
                ks = k.decode() if isinstance(k, (bytes, bytearray)) else k
                keys.append(ks[len(self._prefix) + 1:] if self._prefix else ks)
            if cursor == 0:
                break
        return keys
