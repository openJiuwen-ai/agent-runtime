"""
异步 Redis 客户端（redis-py asyncio）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger
from redis.asyncio import Redis


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

    async def set_nx(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """SET if Not eXists。返回 True 表示写入成功（key 之前不存在），False 表示 key 已存在。"""
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
