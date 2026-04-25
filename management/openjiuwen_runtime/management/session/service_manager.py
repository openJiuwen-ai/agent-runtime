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
from .models import MessageType
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
        self._user_route_tasks: set[asyncio.Task[Any]] = set()
        # 已调度空闲回收定时器的实例 service_id 集合，避免重复 arm
        self._service_idle_timer_armed: set[str] = set()

    async def init(self, response_parser: IResponseParser) -> None:
        self._response_parser = response_parser
        logger.info(
            "ServiceManager 已 init: 服务级并发 sc=%s min_idle=%s max=%s service_idle_ttl=%s",
            self._service_concurrency,
            self._min_idle,
            self._max_services,
            self._service_idle_ttl,
        )

    async def start(self) -> None:
        if self._running:
            logger.debug("ServiceManager start 被忽略: 已在运行中")
            return
        self._running = True
        # 消息循环：从双队列取件；用户请求会 spawn 成独立 task，不阻塞下一条入队
        self._message_task = asyncio.create_task(self._message_loop())
        # 补实例：按周期检查是否低于 min_idle
        self._autoscale_task = asyncio.create_task(self._autoscale_loop())
        await self._bootstrap_min_idle()
        logger.info(
            "ServiceManager 已启动, 预拉热完成, 当前实例数=%s", self._total_services()
        )

    async def stop(self) -> None:
        self._running = False
        self._q.mark_closed()
        logger.info("ServiceManager 正在停止: 已标记队列关闭, 在途用户路由任务数=%s", len(self._user_route_tasks))
        for t in (self._message_task, self._autoscale_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        self._message_task = None
        self._autoscale_task = None
        for ut in list(self._user_route_tasks):
            if not ut.done():
                ut.cancel()
        if self._user_route_tasks:
            await asyncio.gather(
                *self._user_route_tasks, return_exceptions=True
            )
        self._user_route_tasks.clear()
        logger.info("ServiceManager 已完全停止")

    async def handle_message(self, msg: SessionRequestWrapper) -> None:
        sreq = msg.session_request
        raw = RawMessage(
            MessageType.USER_REQUEST, msg, priority=sreq.priority
        )
        # 入用户侧队列，系统侧事件走 enqueue_system
        await self._q.put_user(raw)
        logger.debug(
            "ServiceManager 用户消息已入队: session_id=%s request_id=%s user_q~=%s",
            sreq.session_id,
            sreq.request_id,
            self._q.user_qsize(),
        )

    async def enqueue_system(self, event: Any) -> None:
        await self._q.put_system(event)
        logger.debug(
            "ServiceManager 系统消息已入队: type=%s sys_q~=%s",
            type(event).__name__,
            self._q.system_qsize(),
        )

    def _total_services(self) -> int:
        return len(self._in_use) + len(self._idle)

    def _discard_user_route_task(self, task: asyncio.Task[Any]) -> None:
        self._user_route_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("用户路由子任务失败: %s", exc, exc_info=True)

    async def _message_loop(self) -> None:
        while self._running:
            try:
                item: QueueItem = await self._q.get()
            except RuntimeError:
                logger.debug("双队列 get 因关闭退出 message_loop")
                break
            except asyncio.CancelledError:
                logger.debug("message_loop 被取消")
                break
            if isinstance(item, ServiceReclaimEvent):
                logger.info("处理系统事件: 缩容回收 service_id=%s", item.service_id)
                await self._on_service_reclaim(item.service_id)
                continue
            if not isinstance(item, RawMessage):
                logger.debug("跳过非 RawMessage: %s", type(item))
                continue
            if item.message_type == MessageType.USER_REQUEST:
                # 为每条用户消息创建独立协程，使多 session 可并行进入 ServiceHandler
                t = asyncio.create_task(self._handle_user_request(item))
                self._user_route_tasks.add(t)
                t.add_done_callback(self._discard_user_route_task)
                logger.debug("已 spawn 用户路由 task, 当前在途数=%s", len(self._user_route_tasks))

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
                logger.error("autoscale 周期任务异常: %s", e, exc_info=True)

    async def _bootstrap_min_idle(self) -> None:
        if self._min_idle <= 0:
            return
        async with self._lock:
            while self._min_idle > len(self._idle) and self._total_services() < self._max_services:
                h = await self._new_deployed()
                if h is None:
                    logger.error("预拉热失败: factory/deploy 未返回可用实例, 已停止继续拉起")
                    break
                self._idle[h.id] = h
                logger.info("预拉热: 新实例入 idle, service_id=%s", h.id)

    async def _ensure_min_idle(self) -> None:
        if self._min_idle <= 0:
            return
        async with self._lock:
            while self._min_idle > len(self._idle) and self._total_services() < self._max_services:
                h = await self._new_deployed()
                if h is None:
                    break
                self._idle[h.id] = h
                logger.info("autoscale: 新实例入 idle, service_id=%s", h.id)

    async def _new_deployed(self) -> Optional[IServiceHandler]:
        if self._response_parser is None:
            return None
        try:
            h = await self._factory.new_service(self._response_parser)
        except Exception as e:  # noqa: BLE001
            logger.error("创建服务实例失败 (factory): %s", e, exc_info=True)
            return None
        try:
            await h.deploy()
        except Exception as e:  # noqa: BLE001
            logger.error("服务 deploy 失败: %s", e, exc_info=True)
            return None
        logger.debug("新服务 deploy 成功, 待加入池: service_id=%s", h.id)
        return h

    # 单请求在实例上占 1 个服务并发
    _NEED = 1

    async def _handle_user_request(self, raw: RawMessage) -> None:
        w = raw.message
        if not isinstance(w, SessionRequestWrapper):
            logger.error("用户消息体类型错误, 预期 SessionRequestWrapper, 实际 %s", type(w))
            return
        sreq = w.session_request
        session_id = sreq.session_id
        h: Optional[IServiceHandler] = None
        try:
            # 在锁内完成亲和查找 / 新拉实例，避免与池状态竞争；handle_message 在锁外执行
            async with self._lock:
                h = await self._pick_or_create(sreq)
                if h is not None and not h.has_session(session_id):
                    await self._service_router.set_session_service(session_id, h.id)
                    logger.info(
                        "session 已绑定到实例: session_id=%s -> service_id=%s",
                        session_id,
                        h.id,
                    )
            if h is None:
                await self._fail(w, 100001, session_id=session_id)
                return
            logger.debug(
                "路由到服务实例: service_id=%s session_id=%s request_id=%s",
                h.id,
                session_id,
                sreq.request_id,
            )
            await h.handle_message(w)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("路由/处理过程异常, session_id=%s: %s", session_id, e, exc_info=True)
            await self._fail(w, 100002, session_id=session_id)
        else:
            if sreq.session_ttl > 0:
                try:
                    await self._arm_session_timer(session_id, sreq.session_ttl)
                except Exception as e2:  # noqa: BLE001
                    logger.error("arm session 计时器失败: %s", e2, exc_info=True)

    async def _arm_session_timer(self, session_id: str, ttl: int) -> None:
        if ttl <= 0:
            return
        key = f"sess:{session_id}"
        await self._timer.cancel_timer(key)

        async def _expired() -> None:
            await self._on_session_expired(session_id)

        await self._timer.start_timer(key, ttl, _expired)
        logger.info("已 arm session TTL 计时: session_id=%s ttl=%s", session_id, ttl)

    async def _on_session_expired(self, session_id: str) -> None:
        logger.info("session TTL 到期, 开始清理: session_id=%s", session_id)
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
        logger.debug("session TTL 清理结束: session_id=%s", session_id)

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
        logger.info("已 arm 实例空闲回收计时: service_id=%s ttl=%s", service_id, self._service_idle_ttl)

    async def _on_service_reclaim(self, service_id: str) -> None:
        async with self._lock:
            h = self._idle.get(service_id)
            if h is None:
                logger.debug("缩容跳过: idle 中无此实例 service_id=%s", service_id)
                return
            if h.active_session_count > 0 or h.inflight_requests > 0:
                logger.debug(
                    "缩容跳过: 实例仍活跃 session仍=%s inflight=%s",
                    h.active_session_count,
                    h.inflight_requests,
                )
                return
            self._idle.pop(service_id, None)
        try:
            await h.delete()
            logger.info("缩容已删除实例: service_id=%s", service_id)
        except Exception as e:  # noqa: BLE001
            logger.error("缩容 delete 失败: service_id=%s err=%s", service_id, e, exc_info=True)

    async def _pick_or_create(self, sreq) -> Optional[IServiceHandler]:  # noqa: ANN001
        # 1) 亲和：该 session 已绑定到某 service，则复用
        # 2) 否则在 in_use/idle 中找尚有服务级并发的实例
        # 3) 再否则在 max 允许下新 deploy
        need = self._NEED
        session_id = sreq.session_id
        sid = await self._service_router.get_session_service(session_id)
        if sid is not None:
            h = self._in_use.get(sid) or self._idle.get(sid)
            if h is None:
                await self._service_router.delete_session_service(session_id)
                logger.debug("亲和失效: 路由表有记录但池无此实例, 已删映射 session_id=%s", session_id)
            else:
                if h.id in self._idle:
                    self._idle.pop(h.id, None)
                    self._in_use[h.id] = h
                    await self._timer.cancel_timer(f"svc:{h.id}")
                    self._service_idle_timer_armed.discard(h.id)
                    logger.debug("从 idle 取回实例, service_id=%s", h.id)
                return h
        for h in self._in_use.values():
            if h.available_concurrency >= need:
                logger.debug("选用 in_use 实例: service_id=%s avail=%s", h.id, h.available_concurrency)
                return h
        for h in list(self._idle.values()):
            if h.available_concurrency >= need:
                self._idle.pop(h.id, None)
                self._in_use[h.id] = h
                await self._timer.cancel_timer(f"svc:{h.id}")
                self._service_idle_timer_armed.discard(h.id)
                logger.debug("从 idle 唤醒实例: service_id=%s", h.id)
                return h
        if self._total_services() >= self._max_services:
            logger.debug(
                "pick: 未选到可用实例且已达 max_services=%s, 当前总实例=%s",
                self._max_services,
                self._total_services(),
            )
            return None
        h2 = await self._new_deployed()
        if h2 is None:
            return None
        self._in_use[h2.id] = h2
        logger.info("新建实例并入 in_use: service_id=%s 当前总数=%s", h2.id, self._total_services())
        return h2

    async def _fail(
        self, w: SessionRequestWrapper, code: int, *, session_id: str | None = None
    ) -> None:
        # 对客户端可见的失败：通过 response_queue 下发错误并结束 cancel
        em = exception_message(code)
        if code == 100001:
            logger.warning(
                "业务拒绝(资源已满): code=%s session_id=%s %s", em.code, session_id, em.message
            )
        else:
            logger.error(
                "业务失败: code=%s session_id=%s %s", em.code, session_id, em.message
            )
        await w.response_queue.put(
            {
                "error_code": em.code,
                "message": em.message,
                "completed": True,
            }
        )
        if w.cancel and not w.cancel.done():
            w.cancel.set_result(None)
