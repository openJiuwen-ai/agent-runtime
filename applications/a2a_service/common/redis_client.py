"""
异步 Redis 客户端（redis-py asyncio）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger
from redis.asyncio import Redis


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

    async def connect(self, url: str) -> None:
        self._client = Redis.from_url(url, decode_responses=True)
        await self._client.ping()
        logger.info(f"[Redis] 已连接：{url}")

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

    async def acquire_lock(self, lock_key: str, owner_id: str, ttl_seconds: int) -> bool:
        ttl = max(int(ttl_seconds), 1)
        return await self.set_if_not_exists(lock_key, owner_id, ex=ttl)

    async def release_lock(self, lock_key: str, owner_id: str) -> bool:
        result = await self.client.eval(_RELEASE_LOCK_LUA, 1, lock_key, owner_id)
        return int(result or 0) == 1
