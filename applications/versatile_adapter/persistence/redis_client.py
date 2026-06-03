# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
异步 Redis 客户端（redis-py asyncio）。
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from loguru import logger
from redis.asyncio import Redis


class RedisClient:
    """轻量 Redis 封装，仅暴露项目用到的操作。"""

    def __init__(self) -> None:
        self._client: Optional[Redis] = None

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

    async def delete(self, *keys: str) -> None:
        if keys:
            await self.client.delete(*keys)
