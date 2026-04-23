# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Routing scheduler for dispatching sessions to pods."""

from __future__ import annotations

import asyncio
import logging
import time

from .config import DispatchSettings
from .exceptions import CapacityAllocationError, QueueTimeoutError
from .models import PodInfo, PodState, SessionInfo
from .store import RedisDispatchStore

logger = logging.getLogger(__name__)


class Scheduler:
    """Session-aware pod scheduler."""

    QueueTimeoutError = QueueTimeoutError

    def __init__(self, store: RedisDispatchStore, settings: DispatchSettings):
        self.store = store
        self.settings = settings

    async def init(self) -> None:
        self.settings = self.settings.apply_runtime_overrides(await self.store.load_config())

    async def resolve(self, session_id: str, concurrency: int, ttl: int) -> str:
        existing = await self.store.get_session(session_id)
        if existing and existing.bound_pod_id:
            pod = await self.store.get_pod(existing.bound_pod_id)
            if pod and pod.state != PodState.DRAINING:
                await self.store.mark_session_running(session_id)
                return pod.target_url
            await self.store.mark_session_orphaned(session_id)

        deadline = time.monotonic() + self.settings.queue_max_wait
        requested_scale = False
        while True:
            selected = await self._select_and_allocate(session_id=session_id, concurrency=concurrency, ttl=ttl)
            if selected is not None:
                pod, was_idle = selected
                if was_idle:
                    self._fire_and_forget(
                        self.store.enqueue_admin_event("prewarm_consumed", pod_id=pod.pod_id)
                    )
                return pod.target_url

            if not requested_scale:
                await self.store.enqueue_scale_event(
                    "no_capacity",
                    session_id=session_id,
                    concurrency=concurrency,
                    demand=1,
                )
                requested_scale = True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QueueTimeoutError(f"timed out waiting for pod capacity for session {session_id}")

            await self.store.wait_for_pod_ready(timeout=min(remaining, max(self.settings.scale_up_debounce, 0.5)))

    async def enter_ttl_waiting(self, session_id: str) -> None:
        await self.store.enter_ttl_waiting(session_id)

    async def _select_and_allocate(
        self,
        session_id: str,
        concurrency: int,
        ttl: int,
    ) -> tuple[PodInfo, bool] | None:
        pods = await self.store.all_pods()
        candidates = [pod for pod in pods if pod.is_schedulable and pod.available >= concurrency]
        candidates.sort(key=lambda pod: (pod.available, pod.created_at))

        for pod in candidates:
            was_idle = len(pod.bound_sessions) == 0
            session = SessionInfo(
                session_id=session_id,
                concurrency=concurrency,
                ttl_seconds=ttl,
                bound_pod_id=pod.pod_id,
            )
            try:
                allocated = await self.store.allocate_session(pod, session)
                return allocated, was_idle
            except CapacityAllocationError:
                # Another dispatcher may have grabbed this pod's capacity first.
                continue
        return None

    @staticmethod
    def _fire_and_forget(coro: asyncio.Future | asyncio.Task | asyncio.coroutines) -> None:
        task = asyncio.create_task(coro)
        task.add_done_callback(Scheduler._consume_background_exception)

    @staticmethod
    def _consume_background_exception(task: asyncio.Task) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.warning("background admin event failed: %s", exc)
