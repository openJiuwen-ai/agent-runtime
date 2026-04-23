# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Redis-backed EventBus implementation."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from .base import EventBus


class RedisEventBus(EventBus):
    """Redis Streams + PubSub implementation."""

    def __init__(self, redis_client: Any):
        self._redis = redis_client

    async def enqueue(self, topic: str, payload: dict[str, str]) -> str:
        return await self._redis.xadd(topic, payload)

    async def consume(
        self,
        topic: str,
        cursor: str,
        count: int,
        block_ms: int,
    ) -> list[tuple[str, dict[str, str]]]:
        streams = await self._redis.xread({topic: cursor}, count=count, block=block_ms)
        result: list[tuple[str, dict[str, str]]] = []
        for _, entries in streams or []:
            for msg_id, fields in entries:
                result.append((msg_id, fields))
        return result

    async def publish(self, channel: str, payload: str) -> None:
        await self._redis.publish(channel, payload)

    def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, str]]:
        async def iterator() -> AsyncIterator[tuple[str, str]]:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(*channels)
            try:
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if not message:
                        await asyncio.sleep(0.01)
                        continue
                    if message.get("type") != "message":
                        continue
                    channel_name = message.get("channel")
                    payload = message.get("data")
                    if isinstance(channel_name, bytes):
                        channel_name = channel_name.decode()
                    if isinstance(payload, bytes):
                        payload = payload.decode()
                    yield str(channel_name), str(payload)
            finally:
                await pubsub.unsubscribe(*channels)
                if hasattr(pubsub, "aclose"):
                    await pubsub.aclose()
                else:  # pragma: no cover - compatibility path for older redis clients
                    await pubsub.close()

        return iterator()

