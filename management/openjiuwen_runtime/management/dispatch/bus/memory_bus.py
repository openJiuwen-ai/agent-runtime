# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""In-memory EventBus implementation used by tests and local development."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import AsyncIterator

from .base import EventBus


class InMemoryEventBus(EventBus):
    """A lightweight EventBus without external dependencies."""

    def __init__(self):
        self._streams: dict[str, list[tuple[str, dict[str, str]]]] = defaultdict(list)
        self._conditions: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)
        self._subscribers: dict[str, set[asyncio.Queue[tuple[str, str]]]] = defaultdict(set)
        self._counter = 0

    async def enqueue(self, topic: str, payload: dict[str, str]) -> str:
        self._counter += 1
        msg_id = f"{self._counter}-0"
        self._streams[topic].append((msg_id, payload))
        async with self._conditions[topic]:
            self._conditions[topic].notify_all()
        return msg_id

    async def consume(
        self,
        topic: str,
        cursor: str,
        count: int,
        block_ms: int,
    ) -> list[tuple[str, dict[str, str]]]:
        deadline = time.monotonic() + (block_ms / 1000.0)
        while True:
            entries = [
                item for item in self._streams[topic]
                if self._is_after(item[0], cursor)
            ][:count]
            if entries or block_ms == 0:
                return entries

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            async with self._conditions[topic]:
                try:
                    await asyncio.wait_for(self._conditions[topic].wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return []

    async def publish(self, channel: str, payload: str) -> None:
        for queue in list(self._subscribers[channel]):
            await queue.put((channel, payload))

    def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, str]]:
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        for channel in channels:
            self._subscribers[channel].add(queue)

        async def iterator() -> AsyncIterator[tuple[str, str]]:
            try:
                while True:
                    yield await queue.get()
            finally:
                for channel in channels:
                    self._subscribers[channel].discard(queue)

        return iterator()

    @staticmethod
    def _is_after(message_id: str, cursor: str) -> bool:
        left_major, _, left_minor = message_id.partition("-")
        right_major, _, right_minor = cursor.partition("-")
        return (int(left_major), int(left_minor or 0)) > (int(right_major), int(right_minor or 0))

