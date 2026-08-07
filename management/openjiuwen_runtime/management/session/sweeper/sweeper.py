# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""一轮 sweep：Pass A 解绑到期会话 + Pass B 空 Pod 通知（P0+P1）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Set, Tuple

from openjiuwen_runtime.foundation.log import get_logger

from .store import EvictResult, ExpiryStore

logger = get_logger(__name__)


class ResourceNotifier(Protocol):
    async def idle_consider(self, endpoint_id: str, service_id: str = "") -> None: ...


@dataclass
class SweepStats:
    expired_count: int = 0
    evict_ok: int = 0
    evict_fail: int = 0
    idle_consider_count: int = 0
    duration_ms: float = 0.0
    emptied_pods: List[Tuple[str, str]] = field(default_factory=list)


class Sweeper:
    """执行单轮老化与空 Pod 通知。"""

    def __init__(self, store: ExpiryStore, resource: ResourceNotifier) -> None:
        self._store = store
        self._resource = resource

    async def sweep_once(self, now: Optional[float] = None) -> SweepStats:
        t0 = time.monotonic()
        stats = SweepStats()
        expired = await self._store.list_expired(now=now)
        stats.expired_count = len(expired)
        emptied: Set[Tuple[str, str]] = set()

        for session_id in expired:
            try:
                result: EvictResult = await self._store.evict(session_id)
                if result.evicted:
                    stats.evict_ok += 1
                    if (
                        result.service_id
                        and result.endpoint_id
                        and result.pod_remaining == 0
                    ):
                        emptied.add((result.service_id, result.endpoint_id))
                else:
                    stats.evict_fail += 1
            except Exception:
                stats.evict_fail += 1
                logger.exception("evict failed: session=%s", session_id)

        for service_id, endpoint_id in emptied:
            try:
                if await self._store.try_mark_idle_notified(service_id, endpoint_id):
                    await self._resource.idle_consider(endpoint_id, service_id=service_id)
                    stats.idle_consider_count += 1
                    stats.emptied_pods.append((service_id, endpoint_id))
            except Exception:
                logger.exception(
                    "idle_consider path failed: service=%s endpoint=%s",
                    service_id,
                    endpoint_id,
                )

        stats.duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "sweep_once done: expired=%s ok=%s fail=%s idle_consider=%s duration_ms=%.1f",
            stats.expired_count,
            stats.evict_ok,
            stats.evict_fail,
            stats.idle_consider_count,
            stats.duration_ms,
        )
        return stats
