# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务管理：双 asyncio 队列（系统优先）、单请求=1 服务并发、亲和、起停与缩容。"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, Union

from openjiuwen_runtime.foundation.log import get_logger

from .dual_queue import PriorityDualAsyncQueues
from .exception import exception_message
from .internal_events import ServiceReclaimEvent
from .interfaces import (
    IResponseParser,
    IServiceHandler,
    IServiceInstanceFactory,
    IServiceManager,
    ITimer,
    RawMessage,
    SessionRequestWrapper,
)
from .models import MessagePriority, MessageType
from .router import ServiceRouter

logger = get_logger(__name__)

QueueItem = Union[RawMessage, ServiceReclaimEvent]


class ServiceManager(IServiceManager):
    def __init__(
        self,
        service_factory: IServiceInstanceFactory,
        dual_queue: PriorityDualAsyncQueues[QueueItem],
        timer: ITimer,
        *,
        service_concurrency: int = 200,
        min_idle_services: int = 0,
        max_services: int = 10,
        autoscale_interval: float = 0.5,
        service_idle_ttl: int = 300,
    ) -> None:
        self._factory = service_factory
        self._q = dual_queue
        self._timer = timer
        self._service_concurrency = service_concurrency
        self._min_idle = min_idle_services
        self._max_services = max_services
        self._autoscale_interval = autoscale_interval
        self._service_idle_ttl = service_idle_ttl

        self._response_parser: Optional[IResponseParser] = None
        self._lock = asyncio.Lock()
        self._in_use: dict[str, IServiceHandler] = {}
        self._idle: dict[str, IServiceHandler] = {}
        self._service_router = ServiceRouter()
        self._running = False
        self._message_task: Optional[asyncio.Task[Any]] = None
        self._autoscale_task: Optional[asyncio.Task[Any]] = None
        # 已调度空闲回收定时器的实例
        self._service_idle_timer_armed: set[str] = set()

    async def init(self, response_parser: IResponseParser) -> None:
        self._response_parser = response_parser
        logger.info(
            "ServiceManager init: sc=%s min_idle=%s max=%s idle_ttl=%s",
            self._service_concurrency,
            self._min_idle,
            self._max_services,
            self._service_idle_ttl,
        )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._message_task = asyncio.create_task(self._message_loop())
        self._autoscale_task = asyncio.create_task(self._autoscale_loop())
        await self._bootstrap_min_idle()
        logger.info("ServiceManager started (pre-warm done)")

    async def stop(self) -> None:
        self._running = False
        self._q.mark_closed()
        for t in (self._message_task, self._autoscale_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        self._message_task = None
        self._autoscale_task = None
        logger.info("ServiceManager stopped")

    async def handle_message(self, msg: SessionRequestWrapper) -> None:
        sreq = msg.session_request
        raw = RawMessage(
            MessageType.USER_REQUEST, msg, priority=sreq.priority
        )
        await self._q.put_user(raw)

    async def enqueue_system(self, event: Any) -> None:
        await self._q.put_system(event)

    def _total_services(self) -> int:
        return len(self._in_use) + len(self._idle)

    async def _message_loop(self) -> None:
        while self._running:
            try:
                item: QueueItem = await self._q.get()
            except RuntimeError:
                break
            except asyncio.CancelledError:
                break
            if isinstance(item, ServiceReclaimEvent):
                await self._on_service_reclaim(item.service_id)
                continue
            if not isinstance(item, RawMessage):
                continue
            if item.message_type == MessageType.USER_REQUEST:
                await self._handle_user_request(item)

    async def _autoscale_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._autoscale_interval)
                if not self._running:
                    break
                await self._ensure_min_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error("autoscale error: %s", e)

    async def _bootstrap_min_idle(self) -> None:
        if self._min_idle <= 0:
            return
        async with self._lock:
            while self._min_idle > len(self._idle) and self._total_services() < self._max_services:
                h = await self._new_deployed()
                if h is None:
                    break
                self._idle[h.id] = h

    async def _ensure_min_idle(self) -> None:
        if self._min_idle <= 0:
            return
        async with self._lock:
            while self._min_idle > len(self._idle) and self._total_services() < self._max_services:
                h = await self._new_deployed()
                if h is None:
                    break
                self._idle[h.id] = h

    async def _new_deployed(self) -> Optional[IServiceHandler]:
        if self._response_parser is None:
            return None
        try:
            h = await self._factory.new_service(self._response_parser)
        except Exception as e:  # noqa: BLE001
            logger.error("service factory failed: %s", e)
            return None
        try:
            await h.deploy()
        except Exception as e:  # noqa: BLE001
            logger.error("service deploy failed: %s", e)
            return None
        return h

    # 单请求在实例上占 1 个服务并发
    _NEED = 1

    async def _handle_user_request(self, raw: RawMessage) -> None:
        w = raw.message
        if not isinstance(w, SessionRequestWrapper):
            return
        sreq = w.session_request
        session_id = sreq.session_id
        h: Optional[IServiceHandler] = None
        try:
            async with self._lock:
                h = await self._pick_or_create(sreq)
                if h is None:
                    await self._fail(w, 100001)
                else:
                    if not h.has_session(session_id):
                        await self._service_router.set_session_service(session_id, h.id)
                    await h.handle_message(w)
        except Exception as e:  # noqa: BLE001
            logger.error("route error: %s", e, exc_info=True)
            await self._fail(w, 100002)
        else:
            if h is not None and sreq.session_ttl > 0:
                try:
                    await self._arm_session_timer(session_id, sreq.session_ttl)
                except Exception as e2:  # noqa: BLE001
                    logger.error("arm session timer: %s", e2)

    async def _arm_session_timer(self, session_id: str, ttl: int) -> None:
        if ttl <= 0:
            return
        key = f"sess:{session_id}"
        await self._timer.cancel_timer(key)

        async def _expired() -> None:
            await self._on_session_expired(session_id)

        await self._timer.start_timer(key, ttl, _expired)

    async def _on_session_expired(self, session_id: str) -> None:
        async with self._lock:
            stored = await self._service_router.get_session_service(session_id)
            if not stored:
                return
            h = self._in_use.get(stored) or self._idle.get(stored)
            if h is None:
                await self._service_router.delete_session_service(session_id)
                return
            await h.remove_session(session_id)
            await self._service_router.delete_session_service(session_id)
            if h.active_session_count == 0 and h.id in self._in_use:
                self._in_use.pop(h.id, None)
                self._idle[h.id] = h
            if (
                h.active_session_count == 0
                and h.inflight_requests == 0
                and h.id in self._idle
            ):
                await self._arm_service_idle(h.id)

    async def _arm_service_idle(self, service_id: str) -> None:
        if self._service_idle_ttl <= 0:
            return
        if service_id in self._service_idle_timer_armed:
            return
        self._service_idle_timer_armed.add(service_id)
        key = f"svc:{service_id}"
        await self._timer.cancel_timer(key)

        async def _go() -> None:
            self._service_idle_timer_armed.discard(service_id)
            await self.enqueue_system(ServiceReclaimEvent(service_id=service_id))

        await self._timer.start_timer(key, self._service_idle_ttl, _go)

    async def _on_service_reclaim(self, service_id: str) -> None:
        async with self._lock:
            h = self._idle.get(service_id)
            if h is None:
                return
            if h.active_session_count > 0 or h.inflight_requests > 0:
                return
            self._idle.pop(service_id, None)
        try:
            await h.delete()
        except Exception as e:  # noqa: BLE001
            logger.error("reclaim service %s: %s", service_id, e)

    async def _pick_or_create(self, sreq) -> Optional[IServiceHandler]:  # noqa: ANN001
        need = self._NEED
        session_id = sreq.session_id
        sid = await self._service_router.get_session_service(session_id)
        if sid is not None:
            h = self._in_use.get(sid) or self._idle.get(sid)
            if h is None:
                await self._service_router.delete_session_service(session_id)
            else:
                if h.id in self._idle:
                    self._idle.pop(h.id, None)
                    self._in_use[h.id] = h
                    await self._timer.cancel_timer(f"svc:{h.id}")
                    self._service_idle_timer_armed.discard(h.id)
                return h
        for h in self._in_use.values():
            if h.available_concurrency >= need:
                return h
        for h in list(self._idle.values()):
            if h.available_concurrency >= need:
                self._idle.pop(h.id, None)
                self._in_use[h.id] = h
                await self._timer.cancel_timer(f"svc:{h.id}")
                self._service_idle_timer_armed.discard(h.id)
                return h
        if self._total_services() >= self._max_services:
            return None
        h2 = await self._new_deployed()
        if h2 is None:
            return None
        self._in_use[h2.id] = h2
        return h2

    async def _fail(self, w: SessionRequestWrapper, code: int) -> None:
        em = exception_message(code)
        await w.response_queue.put(
            {
                "error_code": em.code,
                "message": em.message,
                "completed": True,
            }
        )
        if w.cancel and not w.cancel.done():
            w.cancel.set_result(None)
