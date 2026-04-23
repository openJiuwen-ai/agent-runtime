# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Event bus abstraction for dispatch control-plane and data-plane signals."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


class EventBus(ABC):
    """Queue and publish-subscribe semantics used by dispatch."""

    @abstractmethod
    async def enqueue(self, topic: str, payload: dict[str, str]) -> str:
        """Persist a queue event and return its message id."""

    @abstractmethod
    async def consume(
        self,
        topic: str,
        cursor: str,
        count: int,
        block_ms: int,
    ) -> list[tuple[str, dict[str, str]]]:
        """Consume queue events after the provided cursor."""

    @abstractmethod
    async def publish(self, channel: str, payload: str) -> None:
        """Broadcast a transient notification."""

    @abstractmethod
    def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, str]]:
        """Subscribe to transient notifications until cancelled."""

    async def close(self) -> None:
        """Release bus-owned resources."""

