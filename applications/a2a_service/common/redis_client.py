# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
异步 Redis 客户端（redis-py asyncio）。

release_lock 在真 Redis 上走原子 Lua；EVAL 被禁（rename-command / ACL）
的环境惰性降级为 WATCH/MULTI/EXEC 的 owner 校验式 CAS（单键，语义等价）。
"""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import ResponseError, WatchError


_RELEASE_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisClient:
    """轻量 Redis 封装，仅暴露项目用到的操作。"""

    def __init__(self) -> None:
        self._client: Optional[Redis] = None
        # EVAL 禁用 memo（首撞后置位，后续直接走 WATCH 路径）
        self._eval_disabled: bool = False

    async def connect(self, url: str) -> None:
        self._client = Redis.from_url(url, decode_responses=True, protocol=2)
        await self._client.ping()
        safe_url = url
        parsed = urlsplit(url)
        if parsed.password is not None:
            username = parsed.username or ""
            host = parsed.hostname or ""
            if host and ":" in host and not host.startswith("["):
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port is not None else ""
            masked_netloc = f"{username}:@{host}{port}"
            safe_url = urlunsplit((parsed.scheme, masked_netloc, parsed.path, parsed.query, parsed.fragment))
        logger.info(f"[Redis] 已连接：{safe_url}")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("[Redis] 连接已关闭")

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("RedisClient 未连接，请先调用 connect()")
        return self._client

    async def get(self, key: str) -> Optional[str]:
        return await self.client.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        await self.client.set(key, value, ex=ex)

    async def set_if_not_exists(self, key: str, value: str, ex: int) -> bool:
        result = await self.client.set(key, value, ex=ex, nx=True)
        return bool(result)

    async def set_nx(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """兼容旧调用方式：SET if Not eXists。"""
        if ex is None:
            result = await self.client.set(key, value, nx=True)
            return result is not None
        result = await self.client.set(key, value, ex=ex, nx=True)
        return result is not None

    async def delete(self, *keys: str) -> None:
        if keys:
            await self.client.delete(*keys)

    async def get_json(self, key: str) -> Optional[Any]:
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[Redis] JSON 解析失败：key={key}")
            return None

    async def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        await self.set(key, json.dumps(value, ensure_ascii=False), ex=ex)

    async def incr(self, key: str) -> int:
        return int(await self.client.incr(key))

    async def expire(self, key: str, seconds: int) -> bool:
        ttl = max(int(seconds), 1)
        return bool(await self.client.expire(key, ttl))

    async def acquire_lock(self, lock_key: str, owner_id: str, ttl_seconds: int) -> bool:
        ttl = max(int(ttl_seconds), 1)
        return await self.set_if_not_exists(lock_key, owner_id, ex=ttl)

    async def release_lock(self, lock_key: str, owner_id: str) -> bool:
        """释放锁（仅持有者，防误删他人锁）。EVAL 禁用环境降级 WATCH-CAS。"""
        if not self._eval_disabled:
            try:
                result = await self.client.eval(
                    _RELEASE_LOCK_LUA, 1, lock_key, owner_id)
                return int(result or 0) == 1
            except ResponseError as exc:
                msg = str(exc).lower()
                if not (("unknown command" in msg or "noperm" in msg
                         or "no permissions" in msg) and "eval" in msg):
                    raise
                self._eval_disabled = True
                logger.warning(f"[Redis] EVAL 已禁用，release_lock 降级 WATCH 路径：{exc}")
        return await self._release_lock_watch(lock_key, owner_id)

    async def _release_lock_watch(self, lock_key: str, owner_id: str) -> bool:
        """release 的 WATCH 版：GET==owner 才 DEL（decode_responses=True → str）。"""
        async with self.client.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(lock_key)
                current = await pipe.get(lock_key)
                if current != owner_id:
                    await pipe.unwatch()
                    return False
                pipe.multi()
                pipe.delete(lock_key)
                resp = await pipe.execute()
                return bool(resp and resp[0])
            except WatchError:
                return False
