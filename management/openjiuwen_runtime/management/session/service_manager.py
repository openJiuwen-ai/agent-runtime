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
        self._generation: int = 0

        self._response_parser: Optional[IResponseParser] = None
        self._lock = asyncio.Lock()
        self._in_use: dict[str, IServiceHandler] = {}
        self._idle: dict[str, IServiceHandler] = {}
        self._service_router = ServiceRouter()
        self._running = False
        self._message_task: Optional[asyncio.Task[Any]] = None
        self._autoscale_task: Optional[asyncio.Task[Any]] = None
        self._user_route_tasks: set[asyncio.Task[Any]] = set()
        # 已 arm「in_use → idle」计时的 service_id，避免对同一实例重复开多个计时器
        self._to_idle_timer_armed: set[str] = set()
        # 已 arm「多余 idle 回收」的 service_id，避免对同一台重复入队/定时
        self._excess_idle_timer_armed: set[str] = set()
        # session_ttl 已到期但当时该 session 仍有 inflight, 待 _complete/新消息后清理
        # 形如 {session_id: service_id}; 接到新消息会自动清掉, _complete 钩子会触发 flush
        self._pending_expired_sessions: dict[str, str] = {}
        self._stop_completed: bool = False

    async def init(self, response_parser: IResponseParser) -> None:
        self._response_parser = response_parser
        logger.info(
            "ServiceManager 已 init: sc=%s min_idle=%s max=%s "
            "service_ttl(in_use 全空→idle 等待秒数; 入 idle 后超 min 立即回收, 不二次等待)=%s",
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
        """停 message/autoscale/用户子任务、取消全部计时、delete 所有 in_use/idle 实例并清亲和表；幂等。"""
        if self._stop_completed:
            logger.debug("ServiceManager stop 被忽略: 已停止")
            return
        self._stop_completed = True
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
        self._to_idle_timer_armed.clear()
        self._excess_idle_timer_armed.clear()
        self._pending_expired_sessions.clear()
        try:
            await self._timer.stop_all()
        except Exception as e:  # noqa: BLE001
            logger.debug("Timer.stop_all: %s", e)
        n_release = 0
        all_handlers: list[IServiceHandler] = []
        async with self._lock:
            all_handlers.extend(self._in_use.values())
            all_handlers.extend(self._idle.values())
            self._in_use.clear()
            self._idle.clear()
        n_release = len(all_handlers)
        for h in all_handlers:
            try:
                await h.delete()
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "停服时 delete 服务实例失败: service_id=%s err=%s", h.id, e, exc_info=True
                )
        try:
            await self._service_router.clear()
        except Exception as e:  # noqa: BLE001
            logger.error("ServiceRouter clear 失败: %s", e, exc_info=True)
        logger.info("ServiceManager 已完全停止, 已释放 %s 个服务实例", n_release)

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

    async def update_config(self, **kwargs) -> None:
        async with self._lock:
            self._generation += 1
            for key, value in kwargs.items():
                if key == "min_idle_services":
                    self._min_idle = value
                elif key == "max_services":
                    self._max_services = value
                elif key == "service_idle_ttl":
                    self._service_idle_ttl = value
                elif key == "autoscale_interval":
                    self._autoscale_interval = value
            # 立即回收旧代际 idle service（无 session/inflight，安全删除）
            to_reclaim = [
                sid for sid, h in self._idle.items()
                if h._generation != self._generation
                and h.active_session_count == 0
                and h.inflight_requests == 0
            ]
            for sid in to_reclaim:
                h = self._idle.pop(sid, None)
                if h:
                    try:
                        await h.delete()
                        logger.info(
                            "热更新: 已回收旧代际 idle service: service_id=%s old_gen=%s new_gen=%s",
                            sid, h._generation, self._generation,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.error("回收旧代际 idle service 失败: service_id=%s err=%s", sid, e)
            logger.info(
                "ServiceManager 配置已更新: generation=%s min_idle=%s max=%s idle_ttl=%s autoscale=%s",
                self._generation,
                self._min_idle,
                self._max_services,
                self._service_idle_ttl,
                self._autoscale_interval,
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
            cur_gen_idle = sum(1 for h in self._idle.values() if h._generation == self._generation)
            while cur_gen_idle < self._min_idle and self._total_services() < self._max_services:
                h = await self._new_deployed()
                if h is None:
                    logger.error("预拉热失败: factory/deploy 未返回可用实例, 已停止继续拉起")
                    break
                self._idle[h.id] = h
                cur_gen_idle += 1
                logger.info("预拉热: 新实例入 idle, service_id=%s gen=%s", h.id, h._generation)
        # 预拉热入 idle 的实例不启动「in_use→idle / 删 Pod」的 service_ttl 计时

    async def _ensure_min_idle(self) -> None:
        if self._min_idle <= 0:
            return
        _first_gap = True
        async with self._lock:
            cur_gen_idle = sum(1 for h in self._idle.values() if h._generation == self._generation)
            while cur_gen_idle < self._min_idle and self._total_services() < self._max_services:
                if _first_gap:
                    _first_gap = False
                    logger.debug(
                        "autoscale: min_idle=%s 但当前同代际 idle=%s (total=%s), 将补发新实例以维持热备",
                        self._min_idle,
                        cur_gen_idle,
                        self._total_services(),
                    )
                h = await self._new_deployed()
                if h is None:
                    break
                self._idle[h.id] = h
                cur_gen_idle += 1
                logger.info("autoscale: 新实例入 idle, service_id=%s gen=%s", h.id, h._generation)
        # 新入 idle 的实例不启动 service_ttl 删 Pod；仅 min_idle 维持数量

    async def _new_deployed(self) -> Optional[IServiceHandler]:
        if self._response_parser is None:
            return None
        try:
            h = await self._factory.new_service(self._response_parser)
        except Exception as e:  # noqa: BLE001
            logger.error("创建服务实例失败 (factory): %s", e, exc_info=True)
            return None
        h._generation = self._generation
        h.set_idle_pool_transition_hook(self._on_in_use_may_move_to_idle_pool)
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
        # 新消息进入：先取消该 session 待生效的 session_ttl 计时与延期清理标记
        # 满足规则「session_ttl 之内有 session 的消息，则删除 session_ttl 定时器」
        await self._timer.cancel_timer(f"sess:{session_id}")
        self._pending_expired_sessions.pop(session_id, None)
        h: Optional[IServiceHandler] = None
        try:
            # 在锁内完成亲和查找 / 新拉实例，避免与池状态竞争；handle_message 在锁外执行
            async with self._lock:
                h = await self._pick_or_create(sreq)
            if h is not None:
                # 新消息分配到该服务：取消 service_ttl 计时（in_use→idle 转入）
                await self._cancel_in_use_to_idle_timer(h.id)
            async with self._lock:
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
            # 请求结束: 按 session_ttl 维持映射；ttl<=0 视为「不保留」立即标记 pending 等待 flush
            if sreq.session_ttl > 0:
                try:
                    await self._arm_session_timer(session_id, sreq.session_ttl)
                except Exception as e2:  # noqa: BLE001
                    logger.error("arm session 计时器失败: %s", e2, exc_info=True)
            elif h is not None:
                self._pending_expired_sessions[session_id] = h.id
                # 由 _complete 钩子触发 flush；此处主动尝试一次以应对 _complete 已先于本行返回
                await self._flush_pending_expired_for_service(h.id)

    async def _arm_session_timer(self, session_id: str, ttl: int) -> None:
        if ttl <= 0:
            return
        key = f"sess:{session_id}"
        await self._timer.cancel_timer(key)
        # 重新 arm 等同于「session 又活跃」, 清掉旧的延期清理标记
        self._pending_expired_sessions.pop(session_id, None)

        async def _expired() -> None:
            await self._on_session_expired(session_id)

        await self._timer.start_timer(key, ttl, _expired)
        logger.info("已 arm session TTL 计时: session_id=%s ttl=%s", session_id, ttl)

    async def _on_session_expired(self, session_id: str) -> None:
        """session_ttl 到期: 若该 session 已无 inflight 则立即移除并触发 flush；否则入 pending 等待 _complete 后由 hook flush。"""
        logger.info("session TTL 到期, 准备回收: session_id=%s", session_id)
        target_svc: Optional[str] = None
        removed = False
        async with self._lock:
            stored = await self._service_router.get_session_service(session_id)
            if not stored:
                return
            h = self._in_use.get(stored) or self._idle.get(stored)
            if h is None:
                await self._service_router.delete_session_service(session_id)
                return
            target_svc = stored
            if h.session_active_request_count(session_id) > 0:
                # 仍有飞行中的请求, 暂不移除以避免取消正在处理的请求
                self._pending_expired_sessions[session_id] = stored
                logger.info(
                    "session 到期但仍有 inflight, 入延期清理队列: session_id=%s service_id=%s rids=%s",
                    session_id,
                    stored,
                    h.session_active_request_count(session_id),
                )
                return
            await h.remove_session(session_id)
            await self._service_router.delete_session_service(session_id)
            self._pending_expired_sessions.pop(session_id, None)
            removed = True
            logger.info(
                "session 已移除并归还 service 并发: session_id=%s service_id=%s 剩余sessions=%s",
                session_id,
                stored,
                h.active_session_count,
            )
        if removed and target_svc:
            await self._flush_pending_expired_for_service(target_svc)

    async def _flush_pending_expired_for_service(self, service_id: str) -> None:
        """清理 service 上所有「到期但仍 inflight」的 session；若清理后 sessions=0 且 inflight=0, arm service_ttl。"""
        h: Optional[IServiceHandler] = None
        async with self._lock:
            h = self._in_use.get(service_id) or self._idle.get(service_id)
        if h is None:
            return
        async with self._lock:
            sids = [
                sid
                for sid, svc in list(self._pending_expired_sessions.items())
                if svc == service_id
            ]
            for sid in sids:
                if h.session_active_request_count(sid) == 0:
                    await h.remove_session(sid)
                    await self._service_router.delete_session_service(sid)
                    self._pending_expired_sessions.pop(sid, None)
                    logger.info(
                        "延期清理已移除 session: session_id=%s service_id=%s 剩余sessions=%s",
                        sid,
                        service_id,
                        h.active_session_count,
                    )
        # 仅 in_use 上需 arm service_ttl；arm 内部还会再判一次状态防竞争
        if service_id in self._in_use:
            await self._arm_in_use_to_idle_pool(service_id)

    async def _on_in_use_may_move_to_idle_pool(self, service_id: str) -> None:
        """ServiceHandler 在 inflight=0 时回调: 推动延期 session 清理并按状态 arm service_ttl。"""
        await self._flush_pending_expired_for_service(service_id)

    async def _cancel_in_use_to_idle_timer(self, service_id: str) -> None:
        self._to_idle_timer_armed.discard(service_id)
        await self._timer.cancel_timer(f"to_idle:svc:{service_id}")

    async def _cancel_excess_idle_timer(self, service_id: str) -> None:
        self._excess_idle_timer_armed.discard(service_id)
        await self._timer.cancel_timer(f"excess_idle:svc:{service_id}")

    async def _arm_in_use_to_idle_pool(self, service_id: str) -> None:
        """in_use 实例的所有 session 均已归还且无 in-flight: 等待 service_ttl 后转入 _idle。"""
        if self._service_idle_ttl < 0:
            return
        async with self._lock:
            h = self._in_use.get(service_id)
            if h is None:
                return
            if h.inflight_requests > 0 or h.active_session_count > 0:
                # 仍有业务/会话占用, 不 arm
                return
        if self._service_idle_ttl == 0:
            await self._move_in_use_to_idle_pool(service_id)
            return
        if service_id in self._to_idle_timer_armed:
            return
        self._to_idle_timer_armed.add(service_id)
        key = f"to_idle:svc:{service_id}"
        await self._timer.cancel_timer(key)

        async def _go() -> None:
            self._to_idle_timer_armed.discard(service_id)
            await self._move_in_use_to_idle_pool(service_id)

        await self._timer.start_timer(key, self._service_idle_ttl, _go)
        logger.info(
            "已 arm 无业务后转入 idle 池: service_id=%s 等待 %s 秒 (若入池后超 min，回收可与该等待合并，不再双计)",
            service_id,
            self._service_idle_ttl,
        )

    async def _move_in_use_to_idle_pool(self, service_id: str) -> None:
        """service_ttl 到期: 若仍无 session/inflight 则转入 idle 池；否则让出（说明 ttl 内来了新业务）。"""
        async with self._lock:
            oh = self._in_use.get(service_id)
            if oh is None:
                return
            if oh.inflight_requests > 0 or oh.active_session_count > 0:
                # service_ttl 期间又被分配了 session/请求, 让出 (不再强行驱逐)
                logger.info(
                    "service_ttl 到期但 service 已重新被占用, 取消转入 idle: service_id=%s sessions=%s inflight=%s",
                    service_id,
                    oh.active_session_count,
                    oh.inflight_requests,
                )
                return
            self._in_use.pop(oh.id, None)
            self._idle[oh.id] = oh
        logger.info(
            "实例已自 in_use 转入 idle 池: service_id=%s, 当前 idle=%s min_idle=%s",
            service_id,
            len(self._idle),
            self._min_idle,
        )
        # 本实例在 in_use 已等满一次 service_ttl, 若 idle>min 直接回收 Pod, 不再二次等待
        await self._schedule_excess_idle_reclaim_if_needed(after_in_use_to_idle=True)

    async def _schedule_excess_idle_reclaim_if_needed(
        self, *, after_in_use_to_idle: bool = False
    ) -> None:
        """当 len(idle) > min_idle 时，回收一台多余 idle：默认再等待 service_ttl；入 idle 后若
        `after_in_use_to_idle` 为 True 则**不再**叠二次 ttl（与 in_use 阶段无业务等待合并）。
        """
        if self._service_idle_ttl < 0:
            return
        candidate: Optional[str] = None
        async with self._lock:
            if len(self._idle) <= self._min_idle:
                return
            for sid in reversed(list(self._idle.keys())):
                if sid not in self._excess_idle_timer_armed:
                    candidate = sid
                    break
        if candidate is None:
            return
        candidate_h = self._idle.get(candidate)
        # 旧代际 idle：立即回收，不等 service_ttl
        if candidate_h is not None and candidate_h._generation != self._generation:
            self._excess_idle_timer_armed.add(candidate)
            await self.enqueue_system(ServiceReclaimEvent(service_id=candidate))
            logger.debug("旧代际 idle 立即回收入队: service_id=%s gen=%s", candidate, candidate_h._generation)
            return
        # in_use 已按同字段等过一次；或显式 service_ttl=0
        if self._service_idle_ttl == 0 or after_in_use_to_idle:
            self._excess_idle_timer_armed.add(candidate)
            await self.enqueue_system(ServiceReclaimEvent(service_id=candidate))
            logger.debug(
                "多余 idle 立即回收入队: service_id=%s (merge_ttl=%s)",
                candidate,
                after_in_use_to_idle,
            )
            return
        self._excess_idle_timer_armed.add(candidate)
        key = f"excess_idle:svc:{candidate}"
        await self._timer.cancel_timer(key)

        async def _go() -> None:
            self._excess_idle_timer_armed.discard(candidate)
            async with self._lock:
                if len(self._idle) <= self._min_idle or candidate not in self._idle:
                    return
            await self.enqueue_system(ServiceReclaimEvent(service_id=candidate))

        await self._timer.start_timer(key, self._service_idle_ttl, _go)
        logger.info(
            "已 arm 多余 idle 回收: service_id=%s ttl=%s (idle>min=%s)",
            candidate,
            self._service_idle_ttl,
            self._min_idle,
        )

    async def _on_service_reclaim(self, service_id: str) -> None:
        self._excess_idle_timer_armed.discard(service_id)
        h: Optional[IServiceHandler] = None
        should_delete = False
        async with self._lock:
            oh = self._idle.get(service_id)
            if oh is None:
                logger.debug("缩容跳过: idle 中无此实例 service_id=%s", service_id)
                return
            if oh.active_session_count > 0 or oh.inflight_requests > 0:
                logger.debug(
                    "缩容跳过: 实例仍活跃 session仍=%s inflight=%s",
                    oh.active_session_count,
                    oh.inflight_requests,
                )
                return
            if len(self._idle) <= self._min_idle:
                logger.debug(
                    "缩容跳过: idle~=%s 已不大于 min_idle=%s，不删以保常驻底数",
                    len(self._idle),
                    self._min_idle,
                )
                return
            self._idle.pop(service_id, None)
            h = oh
            should_delete = True
        if not should_delete or h is None:
            return
        try:
            await h.delete()
            logger.info("缩容已删除 idle 实例: service_id=%s (多余或系统事件)", service_id)
        except Exception as e:  # noqa: BLE001
            logger.error("缩容 delete 失败: service_id=%s err=%s", service_id, e, exc_info=True)
            return
        await self._schedule_excess_idle_reclaim_if_needed()

    async def _pick_or_create(self, sreq) -> Optional[IServiceHandler]:  # noqa: ANN001
        # 1) 亲和：该 session 已绑定到某 service，则复用（不管代际）
        # 2) 否则在 in_use/idle 中找同代际且尚有服务级并发的实例
        # 3) 再否则在 max 允许下新 deploy（带当前 generation）
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
                    await self._cancel_in_use_to_idle_timer(h.id)
                    await self._cancel_excess_idle_timer(h.id)
                    logger.debug("从 idle 取回实例, service_id=%s", h.id)
                return h
        # 新 session：只选同代际
        for h in self._in_use.values():
            if h._generation == self._generation and h.available_concurrency >= need:
                logger.debug("选用 in_use 实例: service_id=%s gen=%s avail=%s", h.id, h._generation, h.available_concurrency)
                return h
        for h in list(self._idle.values()):
            if h._generation == self._generation and h.available_concurrency >= need:
                self._idle.pop(h.id, None)
                self._in_use[h.id] = h
                await self._cancel_in_use_to_idle_timer(h.id)
                await self._cancel_excess_idle_timer(h.id)
                logger.debug("从 idle 唤醒同代际实例: service_id=%s gen=%s", h.id, h._generation)
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
        logger.info("新建实例并入 in_use: service_id=%s gen=%s 当前总数=%s", h2.id, h2._generation, self._total_services())
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
