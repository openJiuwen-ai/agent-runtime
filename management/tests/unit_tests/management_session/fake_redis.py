# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""内存 Fake Redis（仅覆盖 Sweeper 单测所需命令）。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple


class FakeAsyncRedis:
    """异步风格的极简 Redis，支持 SET NX EX / ZSET / HASH / SET / PUBLISH / 简单 EVAL。"""

    def __init__(self) -> None:
        self.kv: Dict[str, Tuple[str, Optional[float]]] = {}  # key -> (value, expire_at|None)
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.zsets: Dict[str, Dict[str, float]] = {}
        self.sets: Dict[str, Set[str]] = {}
        self.published: List[Tuple[str, str]] = []
        self.now_fn = time.time

    def _purge(self, key: str) -> None:
        item = self.kv.get(key)
        if item and item[1] is not None and item[1] <= self.now_fn():
            del self.kv[key]

    async def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: Optional[int] = None,
        **_: Any,
    ) -> Optional[bool]:
        self._purge(key)
        if nx and key in self.kv:
            return None
        expire_at = self.now_fn() + ex if ex is not None else None
        self.kv[key] = (str(value), expire_at)
        return True

    async def get(self, key: str) -> Optional[str]:
        self._purge(key)
        item = self.kv.get(key)
        return item[0] if item else None

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self.kv:
                del self.kv[k]
                n += 1
            if k in self.hashes:
                del self.hashes[k]
                n += 1
            if k in self.sets:
                del self.sets[k]
                n += 1
            if k in self.zsets:
                del self.zsets[k]
                n += 1
        return n

    async def hset(self, key: str, mapping: Optional[Dict[str, Any]] = None, **kwargs: Any) -> int:
        h = self.hashes.setdefault(key, {})
        data = dict(mapping or {})
        data.update(kwargs)
        for k, v in data.items():
            h[str(k)] = str(v)
        return len(data)

    async def hgetall(self, key: str) -> Dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hget(self, key: str, field: str) -> Optional[str]:
        return self.hashes.get(key, {}).get(field)

    async def sadd(self, key: str, *members: str) -> int:
        s = self.sets.setdefault(key, set())
        before = len(s)
        s.update(str(m) for m in members)
        return len(s) - before

    async def srem(self, key: str, *members: str) -> int:
        s = self.sets.get(key)
        if not s:
            return 0
        n = 0
        for m in members:
            if str(m) in s:
                s.remove(str(m))
                n += 1
        if not s:
            del self.sets[key]
        return n

    async def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    async def smembers(self, key: str) -> Set[str]:
        return set(self.sets.get(key, set()))

    async def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        z = self.zsets.setdefault(key, {})
        for m, score in mapping.items():
            z[str(m)] = float(score)
        return len(mapping)

    async def zrangebyscore(self, key: str, min_score: Any, max_score: Any) -> List[str]:
        z = self.zsets.get(key, {})
        lo = float("-inf") if min_score in ("-inf", None) else float(min_score)
        hi = float("inf") if max_score in ("+inf", None) else float(max_score)
        return [m for m, s in sorted(z.items(), key=lambda x: x[1]) if lo <= s <= hi]

    async def zrem(self, key: str, *members: str) -> int:
        z = self.zsets.get(key)
        if not z:
            return 0
        n = 0
        for m in members:
            if str(m) in z:
                del z[str(m)]
                n += 1
        if not z:
            del self.zsets[key]
        return n

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, str(message)))
        return 1

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])
        if "redis.call('GET', KEYS[1]) == ARGV[1]" in script:
            # release_if_owner
            key, token = keys[0], str(args[0])
            self._purge(key)
            item = self.kv.get(key)
            if item and item[0] == token:
                del self.kv[key]
                return 1
            return 0
        if "HGET" in script and "ZREM" in script and "PUBLISH" in script:
            return await self._eval_evict(keys, args)
        raise NotImplementedError(f"unsupported lua in FakeAsyncRedis: {script[:80]}")

    async def _eval_evict(self, keys: List[Any], args: List[Any]) -> List[Any]:
        sess_key, scope_key, pod_key, expiry_key = (str(k) for k in keys)
        sid, free_ch = str(args[0]), str(args[1])
        if sess_key not in self.hashes:
            await self.zrem(expiry_key, sid)
            return [0, "", "", -1]
        h = self.hashes[sess_key]
        service_id = h.get("service_id", "")
        endpoint_id = h.get("endpoint_id", "")
        await self.srem(scope_key, sid)
        await self.srem(pod_key, sid)
        await self.zrem(expiry_key, sid)
        await self.delete(sess_key)
        remaining = await self.scard(pod_key)
        if free_ch:
            await self.publish(free_ch, "1")
        return [1, service_id, endpoint_id, remaining]
