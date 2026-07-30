# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""发布订阅（Redis Pub/Sub，设计 §9.5）。

瞬时扇出：``await ctx.pubsub.publish(channel, dict)``；``async for msg in ctx.pubsub.subscribe(channel)``。
喊完即逝、不存储——勿与队列（持久交付）混用。用途：leader 交接通知、流式分片跨副本扇出。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator


class PubSub:
    def __init__(self, redis: Any, prefix: str = "pubsub") -> None:
        self._redis = redis
        self._prefix = prefix

    def _chan(self, channel: str) -> str:
        return f"{self._prefix}:{channel}" if self._prefix else channel

    async def publish(self, channel: str, data: dict) -> int:
        """发布事件；返回收到该事件的订阅者数。"""
        return int(await self._redis.publish(self._chan(channel), json.dumps(data)))

    def subscribe(self, channel: str) -> AsyncIterator[dict]:
        """订阅迭代器：每条消息解码为 dict。"""
        return self._subscribe(channel)

    async def _subscribe(self, channel: str) -> AsyncIterator[dict]:
        ch = self._chan(channel)
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(ch)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    raw = msg.get("data")
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode()
                    yield json.loads(raw) if raw else {}
        finally:
            try:
                await pubsub.unsubscribe(ch)
            except Exception:  # noqa: BLE001 - 订阅清理容错
                pass
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if close is not None:
                try:
                    res = close()
                    if hasattr(res, "__await__"):
                        await res
                except Exception:  # noqa: BLE001
                    pass
