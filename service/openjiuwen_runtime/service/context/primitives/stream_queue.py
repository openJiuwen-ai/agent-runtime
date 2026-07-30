# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""队列（Redis Streams + 消费组，设计 §9.4）。

持久、跨副本有序、可回放：``XADD`` / ``XREADGROUP`` / ``XACK``。``ack`` 即成功，不 ack 重投
（at-least-once）。

- ``block=0``：非阻塞，排空即停（便于测试 / 有限消费）。
- ``block>0``：长轮询循环（lifespan worker）。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from redis.exceptions import ResponseError

_DATA_FIELD = "data"


class StreamItem:
    """一条队列消息：``item.data`` 为入队载荷，``await item.ack()`` 确认成功。"""

    def __init__(self, redis: Any, stream: str, group: str, msg_id: bytes | str, data: dict) -> None:
        self._redis = redis
        self._stream = stream
        self._group = group
        self.id = msg_id
        self.data = data

    async def ack(self) -> None:
        await self._redis.xack(self._stream, self._group, self.id)


class StreamQueue:
    def __init__(self, redis: Any, prefix: str = "queue") -> None:
        self._redis = redis
        self._prefix = prefix

    def _stream(self, name: str) -> str:
        return f"{self._prefix}:{name}" if self._prefix else name

    async def enqueue(self, stream: str, data: dict) -> str:
        """入队（持久）。返回消息 id。"""
        return await self._redis.xadd(self._stream(stream), {_DATA_FIELD: json.dumps(data)})

    async def _ensure_group(self, stream: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(self._stream(stream), group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def consume(
        self,
        group: str,
        consumer: str,
        *,
        stream: str,
        block: int = 0,
        count: int = 10,
    ) -> AsyncIterator[StreamItem]:
        """消费迭代器。先排干本 consumer 的 pending（at-least-once），再读新消息。"""
        return self._consume(group, consumer, stream, block, count)

    async def _consume(
        self, group: str, consumer: str, stream: str, block: int, count: int
    ) -> AsyncIterator[StreamItem]:
        key = self._stream(stream)
        await self._ensure_group(stream, group)

        # 1) 排干本 consumer 的 pending（未 ack 重投）
        pending = await self._redis.xreadgroup(group, consumer, {key: "0"}, count=count)
        for _stream_name, messages in pending:
            for msg_id, fields in messages:
                yield self._to_item(key, group, msg_id, fields)

        # 2) 读新消息
        if block == 0:
            resp = await self._redis.xreadgroup(group, consumer, {key: ">"}, count=count)
            for _stream_name, messages in resp:
                for msg_id, fields in messages:
                    yield self._to_item(key, group, msg_id, fields)
            return  # 排空即停

        while True:
            resp = await self._redis.xreadgroup(group, consumer, {key: ">"}, count=count, block=block)
            for _stream_name, messages in resp:
                for msg_id, fields in messages:
                    yield self._to_item(key, group, msg_id, fields)

    def _to_item(self, key: str, group: str, msg_id: Any, fields: dict) -> StreamItem:
        raw = fields.get(_DATA_FIELD) or fields.get(_DATA_FIELD.encode())
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        data = json.loads(raw) if raw else {}
        return StreamItem(self._redis, key, group, msg_id, data)
