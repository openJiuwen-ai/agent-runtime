# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务管理：双 asyncio 队列（系统优先）、按 session 预留服务并发、亲和、起停与缩容。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set, Union
from kubernetes_asyncio import client, config, watch
from kubernetes_asyncio.client.rest import ApiException

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
from .k8s_service_handler import K8sServiceHandler, POD_LABEL_SELECTOR
from .models import MessageType
from .router import ServiceRouter

logger = get_logger(__name__)

QueueItem = Union[RawMessage, ServiceReclaimEvent]

FAILED_POD_STATUSES = {
    "Terminating",
    "Failed",
    "Error",
    "Terminated",
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
}
POD_WATCH_TIMEOUT_SECONDS = 300
POD_WATCH_RECONNECT_DELAY_SECONDS = 3.0


class ServiceManager(IServiceManager):
    def __init__(
        self,
        service_factory: IServiceInstanceFactory,
        dual_queue: PriorityDualAsyncQueues[QueueItem],
        timer: ITimer,
        *,
        service_concurrency: int = 200,
        min_idle_services: int = 1,
        max_services: int = 10,
        autoscale_interval: float = 0.5,
        service_idle_ttl: int = 300,
        service_templates: list[Dict[str, Any]] | None = None,
        # Pod 监控配置
        pod_monitor_enabled: bool = True,
        pod_monitor_interval: float = 10.0,
        namespace: str = "default",
        kubeconfig: Optional[str] = None,
        deploy_mode: str = "k8s",
    ) -> None:
        self._factory = service_factory
        self._q = dual_queue
        self._timer = timer
        self._service_concurrency = service_concurrency
        self._min_idle = min_idle_services
        self._max_services = max_services
        self._autoscale_interval = autoscale_interval
        self._service_idle_ttl = service_idle_ttl

        # Pod 监控配置
        self._pod_monitor_enabled = pod_monitor_enabled
        self._pod_monitor_interval = pod_monitor_interval
        self._namespace = namespace
        self._kubeconfig = kubeconfig
        # 部署模式：仅 k8s 需要 Watch/轮询
        self._deploy_mode = deploy_mode

        self._response_parser: Optional[IResponseParser] = None
        self._lock = asyncio.Lock()
        # 按 template_id 分组的服务实例池: {template_id: {service_id: IServiceHandler}}
        # template_id 为 None 时表示未指定模板的默认组
        self._in_use: dict[Optional[str], dict[str, IServiceHandler]] = {}
        self._idle: dict[Optional[str], dict[str, IServiceHandler]] = {}
        # 服务模板配置列表: [{template_id, min_idle, max_services, ...}]
        self._service_templates: list[Dict[str, Any]] = service_templates or []
        self._service_router = ServiceRouter()
        self._running = False
        self._message_task: Optional[asyncio.Task[Any]] = None
        self._autoscale_task: Optional[asyncio.Task[Any]] = None
        self._pod_monitor_task: Optional[asyncio.Task[Any]] = None
        self._pod_watch_task: Optional[asyncio.Task[Any]] = None
        # 正在被失效 Pod 清理流程删除的 service_id，避免 watch/轮询/缩容/stop 之间重复 delete
        self._deleting_services: set[str] = set()
        self._user_route_tasks: set[asyncio.Task[Any]] = set()
        # 已 arm「in_use → idle」计时的 service_id，避免对同一实例重复开多个计时器
        self._to_idle_timer_armed: set[str] = set()
        # 已 arm「多余 idle 回收」的 service_id，避免对同一台重复入队/定时
        self._excess_idle_timer_armed: set[str] = set()
        # session_ttl 已到期但当时该 session 仍有 inflight, 待 _complete/新消息后清理
        # 形如 {session_id: service_id}; 接到新消息会自动清掉, _complete 钩子会触发 flush
        self._pending_expired_sessions: dict[str, str] = {}
        # deploy 进行中、尚未入 _idle/_in_use 的实例（stop 时必须一并 delete）
        self._deploying: set[IServiceHandler] = set()
        self._stop_completed: bool = False
        # 老化标记：当调用 update_config 时设置为 True，表示此 ServiceManager 待老化
        self._deprecated: bool = False
        # stop 操作的锁，防止并发调用
        self._stop_lock = asyncio.Lock()

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

        async with self._lock:
            # 为所有 template_id 初始化池结构
            for tpl in self._service_templates:
                template_id = tpl.get("template_id")
                if template_id not in self._in_use:
                    self._in_use[template_id] = {}
                if template_id not in self._idle:
                    self._idle[template_id] = {}

            # 确保默认组（None）也存在
            if None not in self._in_use:
                self._in_use[None] = {}
            if None not in self._idle:
                self._idle[None] = {}

        if self._service_templates:
            logger.info(
                "ServiceManager 已加载模板配置: templates_count=%s template_ids=%s",
                len(self._service_templates),
                [tpl.get("template_id") for tpl in self._service_templates],
            )
        else:
            logger.info("ServiceManager 使用默认配置（无模板配置）")

        # 消息循环：从双队列取件；用户请求会 spawn 成独立 task，不阻塞下一条入队
        self._message_task = asyncio.create_task(self._message_loop())
        # 启动 autoscale 循环：定期检查并维护 min_idle、回收多余 idle 实例
        self._autoscale_task = asyncio.create_task(self._autoscale_loop())
        # 仅 k8s 部署模式下启动 Pod 监控循环：定期检测失效 Pod 并清理绑定关系
        if self._pod_monitor_enabled and self._deploy_mode == "k8s":
            self._pod_monitor_task = asyncio.create_task(self._pod_monitor_loop())
            self._pod_watch_task = asyncio.create_task(self._pod_watch_loop())
            logger.info(
                "Pod 监控已启动: poll_interval=%ss watch_timeout=%ss",
                self._pod_monitor_interval,
                POD_WATCH_TIMEOUT_SECONDS,
            )
        # 预拉热：根据模板配置拉起 min_idle 数量的空闲实例
        await self._bootstrap_min_idle()
        logger.info(
            "ServiceManager 已启动, 预拉热完成, 当前实例数=%s", self._total_services()
        )

    async def stop(self) -> None:
        """停 message/autoscale/用户子任务、取消全部计时、delete 所有 in_use/idle 实例并清亲和表；幂等。"""
        # 使用锁防止并发调用 stop()
        async with self._stop_lock:
            if self._stop_completed:
                logger.debug("ServiceManager stop 被忽略: 已停止")
                return
            
            self._running = False
            self._q.mark_closed()
            logger.info("ServiceManager 正在停止: 已标记队列关闭, 在途用户路由任务数=%s", len(self._user_route_tasks))
            for t in (
                self._message_task,
                self._autoscale_task,
                self._pod_monitor_task,
                self._pod_watch_task,
            ):
                if t and not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
            self._message_task = None
            self._autoscale_task = None
            self._pod_monitor_task = None
            self._pod_watch_task = None
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
            self._deleting_services.clear()
            self._pending_expired_sessions.clear()
            try:
                await self._timer.stop_all()
            except Exception as e:  # noqa: BLE001
                logger.debug("Timer.stop_all: %s", e)
            all_handlers: list[IServiceHandler] = []
            async with self._lock:
                # 遍历所有模板组的实例
                for pool in self._in_use.values():
                    all_handlers.extend(pool.values())
                for pool in self._idle.values():
                    all_handlers.extend(pool.values())
                if self._deploying:
                    logger.info(
                        "ServiceManager stop: 另有 %s 个 deploy 进行中的实例待清理",
                        len(self._deploying),
                    )
                    all_handlers.extend(self._deploying)
                self._in_use.clear()
                self._idle.clear()
                self._deploying.clear()
            n_release = 0
            n_failed = 0
            for h in all_handlers:
                try:
                    await h.delete()
                    n_release += 1
                    logger.debug("成功删除服务实例: service_id=%s", h.id)
                except Exception as e:
                    n_failed += 1
                    logger.error(
                        "停服时 delete 服务实例失败: service_id=%s err=%s", h.id, e, exc_info=True
                    )
            
            try:
                await self._service_router.clear()
            except Exception as e:
                logger.error("ServiceRouter clear 失败: %s", e, exc_info=True)
            
            # 只有全部成功才标记为已完成，否则保持 _stop_completed=False 以便重试
            if n_failed == 0:
                self._stop_completed = True
                logger.info(
                    "ServiceManager 已完全停止: 成功释放 %s 个服务实例", n_release
                )
            else:
                logger.warning(
                    "ServiceManager 停止未完成: 成功 %s 个, 失败 %s 个, 总计 %s 个。"
                    "将保持 _stop_completed=False，允许通过 try_cleanup_if_idle() 重试清理",
                    n_release, n_failed, len(all_handlers)
                )

    async def handle_message(self, msg: SessionRequestWrapper) -> None:
        if self._deprecated:
            logger.warning(
                "ServiceManager 已标记为待老化，但仍收到新消息: session_id=%s request_id=%s",
                msg.session_request.session_id,
                msg.session_request.request_id,
            )
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
        """获取所有模板组的总实例数。"""
        total = 0
        for pool in self._in_use.values():
            total += len(pool)
        for pool in self._idle.values():
            total += len(pool)
        return total

    def _get_template_min_idle(self, template_id: Optional[str]) -> int:
        """获取指定 template_id 的最小空闲实例数配置。

        优先使用模板配置中的 min_idle_services 字段，如果不存在则回退到全局配置。
        """
        if not self._service_templates:
            return self._min_idle

        for tpl in self._service_templates:
            if tpl.get("template_id") == template_id:
                # 优先使用模板中的 min_idle_services 字段
                min_idle_val = tpl.get("min_idle_services")
                if min_idle_val is not None:
                    return min_idle_val

        return self._min_idle

    def _get_template_config(self, template_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """获取指定 template_id 的完整模板配置。

        Args:
            template_id: 模板 ID，None 表示默认配置。

        Returns:
            匹配的模板配置字典，如果未找到则返回 None。
        """
        if not self._service_templates:
            return None

        for tpl in self._service_templates:
            if tpl.get("template_id") == template_id:
                return tpl

        return None

    def _get_template_max_services(self, template_id: Optional[str]) -> int:
        """获取指定 template_id 的最大服务实例数配置。

        优先使用模板配置中的 max_services 字段，如果不存在则回退到全局配置。
        """
        if not self._service_templates:
            return self._max_services

        for tpl in self._service_templates:
            if tpl.get("template_id") == template_id:
                # 优先使用模板中的 max_services 字段
                max_services_val = tpl.get("max_services")
                if max_services_val is not None:
                    return max_services_val

        return self._max_services

    def _total_services_by_template(self, template_id: Optional[str]) -> int:
        """获取指定 template_id 的总实例数（in_use + idle）。"""
        in_use_count = len(self._in_use.get(template_id, {}))
        idle_count = len(self._idle.get(template_id, {}))
        return in_use_count + idle_count

    def _idle_count_by_template(self, template_id: Optional[str]) -> int:
        """获取指定 template_id 的空闲实例数。"""
        return len(self._idle.get(template_id, {}))

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
                logger.debug("autoscale 循环被取消")
                break
            except Exception as e:  # noqa: BLE001
                logger.error("autoscale 周期任务异常: %s", e, exc_info=True)

    async def _bootstrap_min_idle(self) -> None:
        if self._min_idle <= 0:
            return

        # 如果没有配置模板，使用默认配置拉起
        if not self._service_templates:
            if self._min_idle <= 0:
                return
            async with self._lock:
                template_id = None
                while self._idle_count_by_template(template_id) < self._min_idle and \
                      self._total_services_by_template(template_id) < self._max_services:
                    h = await self._new_deployed()
                    if h is None:
                        logger.error("预拉热失败: factory/deploy 未返回可用实例, 已停止继续拉起")
                        break
                    self._idle.setdefault(template_id, {})[h.id] = h
                    logger.info("预拉热: 新实例入 idle (default), service_id=%s", h.id)
            return

        # 按每个模板配置分别拉起
        for tpl in self._service_templates:
            template_id = tpl.get("template_id")
            # 优先使用模板配置中的 min_idle_services 字段
            min_idle_services = tpl.get("min_idle_services", self._min_idle)
            max_services = tpl.get("max_services", self._max_services)

            if min_idle_services <= 0:
                continue

            async with self._lock:
                while self._idle_count_by_template(template_id) < min_idle_services and \
                      self._total_services_by_template(template_id) < max_services:
                    h = await self._new_deployed(tpl)
                    if h is None:
                        logger.error(
                            "预拉热失败 (template_id=%s): factory/deploy 未返回可用实例, 已停止继续拉起",
                            template_id
                        )
                        break
                    self._idle.setdefault(template_id, {})[h.id] = h
                    logger.info(
                        "预拉热: 新实例入 idle (template_id=%s), service_id=%s",
                        template_id, h.id
                    )

        # 预拉热入 idle 的实例不启动「in_use→idle / 删 Pod」的 service_ttl 计时

    async def _ensure_min_idle(self) -> None:
        if self._min_idle <= 0:
            return

        # 如果没有配置模板，使用默认配置
        if not self._service_templates:
            if self._min_idle <= 0:
                return
            template_id = None
            _first_gap = True
            async with self._lock:
                while self._idle_count_by_template(template_id) < self._min_idle and \
                      self._total_services_by_template(template_id) < self._max_services:
                    if _first_gap:
                        _first_gap = False
                        logger.debug(
                            "autoscale: min_idle=%s 但当前 idle=%s (total=%s), 将补发新实例以维持热备",
                            self._min_idle,
                            self._idle_count_by_template(template_id),
                            self._total_services_by_template(template_id),
                        )
                    h = await self._new_deployed()
                    if h is None:
                        break
                    self._idle.setdefault(template_id, {})[h.id] = h
                    logger.info("autoscale: 新实例入 idle (default), service_id=%s", h.id)
            return

        # 按每个模板配置分别维护
        for tpl in self._service_templates:
            template_id = tpl.get("template_id")
            # 优先使用模板配置中的 min_idle_services 字段
            min_idle_services = tpl.get("min_idle_services", self._min_idle)
            max_services = tpl.get("max_services", self._max_services)

            if min_idle_services <= 0:
                continue

            _first_gap = True
            async with self._lock:
                while self._idle_count_by_template(template_id) < min_idle_services and \
                      self._total_services_by_template(template_id) < max_services:
                    if _first_gap:
                        _first_gap = False
                        logger.debug(
                            "autoscale (template_id=%s): min_idle_services=%s 但当前 idle=%s (total=%s), 将补发新实例",
                            template_id, min_idle_services,
                            self._idle_count_by_template(template_id),
                            self._total_services_by_template(template_id),
                        )
                    h = await self._new_deployed(tpl)
                    if h is None:
                        break
                    self._idle.setdefault(template_id, {})[h.id] = h
                    logger.info(
                        "autoscale: 新实例入 idle (template_id=%s), service_id=%s",
                        template_id, h.id
                    )
        # 新入 idle 的实例不启动 service_ttl 删 Pod；仅 min_idle 维持数量

    async def _safe_delete_handler(self, h: IServiceHandler) -> None:
        try:
            await h.delete()
        except Exception as e:  # noqa: BLE001
            logger.error(
                "清理服务实例失败: service_id=%s err=%s", h.id, e, exc_info=True
            )

    async def _new_deployed(
        self, service_template: Optional[Dict[str, Any]] = None
    ) -> Optional[IServiceHandler]:
        if self._response_parser is None:
            return None
        try:
            h = await self._factory.new_service(self._response_parser, service_template)
        except Exception as e:  # noqa: BLE001
            logger.error("创建服务实例失败 (factory): %s", e, exc_info=True)
            return None
        h.set_idle_pool_transition_hook(self._on_in_use_may_move_to_idle_pool)
        self._deploying.add(h)
        try:
            await h.deploy()
        except asyncio.CancelledError:
            logger.warning("服务 deploy 被取消: service_id=%s, 正在清理", h.id)
            await self._safe_delete_handler(h)
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("服务 deploy 失败: %s", e, exc_info=True)
            await self._safe_delete_handler(h)
            return None
        finally:
            self._deploying.discard(h)
        logger.debug("新服务 deploy 成功, 待加入池: service_id=%s", h.id)
        return h

    async def _handle_user_request(self, raw: RawMessage) -> None:
        w = raw.message
        if not isinstance(w, SessionRequestWrapper):
            logger.error("用户消息体类型错误, 预期 SessionRequestWrapper, 实际 %s", type(w))
            return
        sreq = w.session_request
        session_id = sreq.session_id
        
        if self._deprecated:
            logger.warning(
                "待老化的 ServiceManager 正在处理新请求: session_id=%s request_id=%s",
                session_id,
                sreq.request_id,
            )
            
        # 新消息进入：先取消该 session 待生效的 session_ttl 计时与延期清理标记
        # 满足规则「session_ttl 之内有 session 的消息，则删除 session_ttl 定时器」
        await self._timer.cancel_timer(f"sess:{session_id}")
        self._pending_expired_sessions.pop(session_id, None)
        h: Optional[IServiceHandler] = None
        # 预留失败但已被 _pick_or_create 放入 _in_use 池的实例: 锁外推动其走 in_use→idle 回收, 避免孤儿泄漏
        failed_to_reserve: Optional[IServiceHandler] = None
        try:
            # 在锁内完成亲和查找 / 新实例 / 新 session 的服务额度预留与路由绑定
            async with self._lock:
                h = await self._pick_or_create(sreq)
                if h is not None:
                    bound_sid = await self._service_router.get_session_service(session_id)
                    if bound_sid is None:
                        if not h.try_reserve_session_quota(
                                session_id, sreq.session_concurrency
                        ):
                            logger.warning(
                                "服务额度预留失败(资源不足): session_id=%s service_id=%s "
                                "need=%s avail=%s",
                                session_id,
                                h.id,
                                sreq.session_concurrency,
                                h.available_concurrency,
                            )
                            # 记录失败的实例, 锁外推动其走 idle 回收流程; 仅置空 h 会导致
                            # _pick_or_create 刚放入 _in_use 的实例成为孤儿 (无 session/inflight
                            # 永不触发 _on_in_use_may_move_to_idle_pool 钩子), Pod 永久驻留
                            failed_to_reserve = h
                            h = None
                        else:
                            await self._service_router.set_session_service(
                                session_id, h.id
                            )
                            logger.info(
                                "session 已绑定到实例: session_id=%s -> service_id=%s",
                                session_id,
                                h.id,
                            )
            if h is not None:
                # 新消息分配到该服务：取消所有相关计时器
                # 1. 取消 in_use→idle 计时（如果实例之前在 in_use 且等待转入 idle）
                # 2. 取消 excess_idle 回收计时（如果实例之前在 idle 且可能被回收）
                await self._cancel_in_use_to_idle_timer(h.id)
                await self._cancel_excess_idle_timer(h.id)

            # 预留失败的实例 (含 _pick_or_create 新 deploy 的): 推动走 in_use→idle 回收
            # service_ttl 到期后转入 idle, 若 idle>min_idle 则被 excess_idle 回收删 Pod;
            # 若 idle<=min_idle 则作为热备保留, 可被后续 need 较小的请求复用
            #
            # 安全性说明:
            # 1. _pick_or_create 分支2(in_use/idle 查找)已用 available>=need 过滤, 返回的实例
            #    try_reserve_session_quota 必成功; 唯一预留失败的是分支3(新 deploy), 是全新的
            #    无 session/inflight。故 failed_to_reserve 通常无其他占用。
            # 2. 即使存在竞态(锁外调用前其他请求在此实例预留成功), _arm_in_use_to_idle_pool
            #    内部有 `inflight>0 or active_session_count>0 则 return` 的保护, 不会错误 arm。
            # 3. 若因有占用而跳过 arm, 该实例由其他请求的 _on_in_use_may_move_to_idle_pool
            #    钩子(inflight 归零时)推动, 不会成为孤儿。
            if failed_to_reserve is not None:
                await self._arm_in_use_to_idle_pool(failed_to_reserve.id)

            if h is None:
                await self._fail(w, 100001, session_id=session_id)
                return
            
            # 获取 Pod 信息用于日志
            pod_name = "unknown"
            if hasattr(h, "pod_info") and h.pod_info:
                pod_name = h.pod_info.pod_name
            
            logger.info(
                "路由到服务实例: service_id=%s session_id=%s request_id=%s pod=%s",
                h.id,
                session_id,
                sreq.request_id,
                pod_name,
            )
            await h.handle_message(w)
            logger.debug(
                "已完成消息处理: service_id=%s session_id=%s request_id=%s pod=%s",
                h.id,
                session_id,
                sreq.request_id,
                pod_name,
            )
        except asyncio.CancelledError:
            logger.error("session_id=%s 被中断执行", session_id)
            await self._fail(w, 100003, session_id=session_id)
        except Exception as e:  # noqa: BLE001
            logger.error("路由/处理过程异常, session_id=%s: %s", session_id, e, exc_info=True)
            await self._fail(w, 100002, session_id=session_id)
        finally:
            # 请求结束: 按 session_ttl 维持映射；ttl<=0 视为「不保留」立即标记 pending 等待 flush
            # 使用 finally 确保无论成功或失败都能正确清理 session
            if h is not None:
                if sreq.session_ttl > 0:
                    try:
                        await self._arm_session_timer(session_id, sreq.session_ttl)
                    except Exception as e2:  # noqa: BLE001
                        logger.error("arm session 计时器失败: %s", e2, exc_info=True)
                    else:
                        logger.debug(
                            "ServiceManager session active: session_id=%s ttl=%s service_id=%s",
                            session_id,
                            sreq.session_ttl,
                            h.id,
                        )
                else:
                    self._pending_expired_sessions[session_id] = h.id
                    logger.debug(
                        "ServiceManager session ttl<=0 pending expired: session_id=%s service_id=%s",
                        session_id,
                        h.id,
                    )
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
            
            # 在所有模板组中查找该 service_id
            h: Optional[IServiceHandler] = self._find_service_handler(stored)
            
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

    def _find_service_handler(self, service_id: str) -> Optional[IServiceHandler]:
        """在所有模板组的 in_use 和 idle 池中查找指定的 service_id。

        Args:
            service_id: 要查找的服务实例 ID。

        Returns:
            找到的 IServiceHandler 实例，如果未找到则返回 None。
        """
        # 先在 in_use 池中查找
        for pool in self._in_use.values():
            if service_id in pool:
                return pool[service_id]
        
        # 再在 idle 池中查找
        for pool in self._idle.values():
            if service_id in pool:
                return pool[service_id]
        
        return None

    async def _flush_pending_expired_for_service(self, service_id: str) -> None:
        """清理 service 上所有「到期但仍 inflight」的 session；若清理后 sessions=0 且 inflight=0, arm service_ttl。"""
        h: Optional[IServiceHandler] = self._find_service_handler(service_id)

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

        # 检查是否在 in_use 中（遍历所有模板组）
        in_in_use = False
        async with self._lock:
            for pool in self._in_use.values():
                if service_id in pool:
                    in_in_use = True
                    break

        if in_in_use:
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
        h: Optional[IServiceHandler] = self._find_service_handler(service_id)

        if h is None:
            return

        async with self._lock:
            if h.inflight_requests > 0 or h.active_session_count > 0:
                # 仍有业务/会话占用, 不 arm
                return

        # 获取 service_ttl，如果为 None 则使用 Manager 的默认值
        service_ttl = h.service_ttl if h.service_ttl is not None else self._service_idle_ttl
        if service_ttl < 0:
            return
        if service_ttl == 0:
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

        await self._timer.start_timer(key, service_ttl, _go)
        logger.info(
            "已 arm 无业务后转入 idle 池: service_id=%s 等待 %s 秒 (若入池后超 min，回收可与该等待合并，不再双计)",
            service_id,
            service_ttl,
        )

    async def _move_in_use_to_idle_pool(self, service_id: str) -> None:
        """service_ttl 到期: 若仍无 session/inflight 则转入 idle 池；否则让出（说明 ttl 内来了新业务）。"""
        template_id: Optional[str] = None
        oh: Optional[IServiceHandler] = None
        moved = False

        async with self._lock:
            # 在锁内查找并移动，避免竞态
            for tid, pool in self._in_use.items():
                if service_id in pool:
                    template_id = tid
                    oh = pool[service_id]
                    break

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

            # 从 in_use 移除，加入 idle
            if template_id is not None:
                self._in_use[template_id].pop(oh.id, None)
                self._idle.setdefault(template_id, {})[oh.id] = oh
                moved = True
            else:
                # 兼容旧逻辑：可能在 None 组中
                for tid, pool in self._in_use.items():
                    if service_id in pool:
                        pool.pop(service_id, None)
                        self._idle.setdefault(tid, {})[service_id] = oh
                        template_id = tid
                        moved = True
                        break

        if not moved:
            return

        min_idle_for_tpl = self._get_template_min_idle(template_id)
        logger.info(
            "实例已自 in_use 转入 idle 池: service_id=%s template_id=%s, 当前 idle=%s min_idle=%s",
            service_id, template_id,
            self._idle_count_by_template(template_id),
            min_idle_for_tpl,
        )
        # 本实例在 in_use 已等满一次 service_ttl, 若 idle>min 直接回收 Pod, 不再二次等待
        await self._schedule_excess_idle_reclaim_if_needed(after_in_use_to_idle=True)

    async def _schedule_excess_idle_reclaim_if_needed(
        self, *, after_in_use_to_idle: bool = False
    ) -> None:
        """当某模板组的 len(idle) > min_idle 时，回收该组的一台多余 idle：默认再等待 service_ttl；入 idle 后若
        `after_in_use_to_idle` 为 True 则**不再**叠二次 ttl（与 in_use 阶段无业务等待合并）。
        """
        candidate: Optional[str] = None
        candidate_h: Optional[IServiceHandler] = None
        candidate_template_id: Optional[str] = None

        async with self._lock:
            # 遍历所有模板组，找到第一个需要回收的组
            for template_id, idle_pool in self._idle.items():
                min_idle_for_tpl = self._get_template_min_idle(template_id)
                if len(idle_pool) <= min_idle_for_tpl:
                    continue

                # 在该组中找到一个未被 arm 的候选实例
                for sid in idle_pool.keys():
                    if sid not in self._excess_idle_timer_armed:
                        candidate = sid
                        candidate_h = idle_pool.get(sid)
                        candidate_template_id = template_id
                        break

                if candidate is not None:
                    break
        if candidate is None or candidate_h is None:
            return

        # 获取该实例的 service_ttl，如果为 None 则使用 Manager 的默认值
        service_ttl = candidate_h.service_ttl if candidate_h.service_ttl is not None else self._service_idle_ttl
        min_idle_for_tpl = self._get_template_min_idle(candidate_template_id)

        # in_use 已按同字段等过一次；或显式 service_ttl=0
        if service_ttl == 0 or after_in_use_to_idle:
            self._excess_idle_timer_armed.add(candidate)
            await self.enqueue_system(ServiceReclaimEvent(service_id=candidate))
            logger.debug(
                "多余 idle 立即回收入队: service_id=%s template_id=%s (merge_ttl=%s)",
                candidate, candidate_template_id, after_in_use_to_idle,
            )
            return
        if service_ttl < 0:
            return

        self._excess_idle_timer_armed.add(candidate)
        key = f"excess_idle:svc:{candidate}"
        await self._timer.cancel_timer(key)

        async def _go() -> None:
            self._excess_idle_timer_armed.discard(candidate)
            async with self._lock:
                idle_pool = self._idle.get(candidate_template_id, {})
                if len(idle_pool) <= min_idle_for_tpl or candidate not in idle_pool:
                    return
            await self.enqueue_system(ServiceReclaimEvent(service_id=candidate))

        await self._timer.start_timer(key, service_ttl, _go)
        logger.info(
            "已 arm 多余 idle 回收: service_id=%s template_id=%s ttl=%s (idle=%s>min=%s)",
            candidate, candidate_template_id, service_ttl,
            self._idle_count_by_template(candidate_template_id),
            min_idle_for_tpl,
        )

    async def _on_service_reclaim(self, service_id: str) -> None:
        self._excess_idle_timer_armed.discard(service_id)
        h: Optional[IServiceHandler] = None
        should_delete = False
        template_id: Optional[str] = None

        async with self._lock:
            # 找到 service_id 所属的 template_id 组
            for tid, idle_pool in self._idle.items():
                if service_id in idle_pool:
                    oh = idle_pool[service_id]
                    template_id = tid
                    break
            else:
                logger.debug("缩容跳过: idle 中无此实例 service_id=%s", service_id)
                return

            if oh.active_session_count > 0 or oh.inflight_requests > 0:
                logger.debug(
                    "缩容跳过: 实例仍活跃 session仍=%s inflight=%s",
                    oh.active_session_count,
                    oh.inflight_requests,
                )
                return

            min_idle_for_tpl = self._get_template_min_idle(template_id)
            if len(self._idle.get(template_id, {})) <= min_idle_for_tpl:
                logger.debug(
                    "缩容跳过: template_id=%s idle~=%s 已不大于 min_idle=%s，不删以保常驻底数",
                    template_id, len(self._idle.get(template_id, {})), min_idle_for_tpl,
                )
                return

            self._idle[template_id].pop(service_id, None)
            h = oh
            should_delete = True
        if not should_delete or h is None:
            return
        try:
            await h.delete()
            logger.info(
                "缩容已删除 idle 实例: service_id=%s template_id=%s (多余或系统事件)",
                service_id, template_id
            )
        except Exception as e:  # noqa: BLE001
            logger.error("缩容 delete 失败: service_id=%s err=%s", service_id, e, exc_info=True)
            return
        await self._schedule_excess_idle_reclaim_if_needed()

    async def _pick_or_create(self, sreq) -> Optional[IServiceHandler]:  # noqa: ANN001
        """根据 session 请求选择或创建服务实例。

        选择策略：
        1) 亲和：该 session 已绑定到某 service，则复用
        2) 否则在相同 template_id 组的 in_use/idle 池中找尚有服务级并发的实例
        3) 再否则在该 template_id 组的 max 允许下新 deploy
        """
        need = max(1, int(sreq.session_concurrency))
        session_id = sreq.session_id

        # 获取 template_id
        template_id: Optional[str] = None
        if sreq.service_template:
            template_id = sreq.service_template.get("template_id")

        # 1) 亲和：该 session 已绑定到某 service，则复用
        sid = await self._service_router.get_session_service(session_id)
        if sid is not None:
            # 在所有模板组中查找该 service_id
            h: Optional[IServiceHandler] = None
            found_template_id: Optional[str] = None
            for tid, pool in self._in_use.items():
                if sid in pool:
                    h = pool[sid]
                    found_template_id = tid
                    break

            if h is None:
                for tid, pool in self._idle.items():
                    if sid in pool:
                        h = pool[sid]
                        found_template_id = tid
                        break

            if h is None:
                await self._service_router.delete_session_service(session_id)
                logger.debug("亲和失效: 路由表有记录但池无此实例, 已删映射 session_id=%s", session_id)
            else:
                # 从 idle 移到 in_use
                if found_template_id is not None:
                    if sid in self._idle.get(found_template_id, {}):
                        self._idle[found_template_id].pop(sid, None)
                        self._in_use.setdefault(found_template_id, {})[sid] = h
                else:
                    # 兼容旧逻辑
                    for tid, pool in self._idle.items():
                        if sid in pool:
                            pool.pop(sid, None)
                            self._in_use.setdefault(tid, {})[sid] = h
                            break

                # 注意：计时器取消由调用者 _handle_user_request 在锁外统一处理
                logger.debug("从 idle 取回实例, service_id=%s template_id=%s", h.id, found_template_id)
                return h

        # 2) 新 session：在相同 template_id 组的 in_use/idle 中找实例
        target_template_id = template_id

        # 先在 in_use 池中查找
        in_use_pool = self._in_use.get(target_template_id, {})
        for h in in_use_pool.values():
            if h.available_concurrency >= need:
                logger.debug(
                    "选用 in_use 实例 (template_id=%s): service_id=%s avail=%s",
                    target_template_id, h.id, h.available_concurrency
                )
                return h

        # 再在 idle 池中查找
        idle_pool = self._idle.get(target_template_id, {})
        for h in list(idle_pool.values()):
            if h.available_concurrency >= need:
                idle_pool.pop(h.id, None)
                self._in_use.setdefault(target_template_id, {})[h.id] = h
                # 注意：计时器取消由调用者 _handle_user_request 在锁外统一处理
                logger.debug(
                    "从 idle 唤醒实例 (template_id=%s): service_id=%s",
                    target_template_id, h.id
                )
                return h

        # 3) 未找到匹配实例，在该 template_id 组的 max 允许下新 deploy
        # 从 _service_templates 中查询获取完整的模板配置
        template_config = self._get_template_config(target_template_id)
        max_services_for_tpl = self._get_template_max_services(target_template_id)
        
        if self._total_services_by_template(target_template_id) >= max_services_for_tpl:
            logger.debug(
                "pick: 未选到可用实例且 template_id=%s 已达 max_services=%s, 当前该组实例=%s",
                target_template_id, max_services_for_tpl,
                self._total_services_by_template(target_template_id),
            )
            return None

        # 使用查询到的模板配置进行部署（如果存在）
        deploy_template = template_config if template_config else sreq.service_template

        # 预检拦截: 单 session 声明的并发 need 若已超过单实例总并发上限, 即使新 deploy
        # 一个 Pod 也无法满足 try_reserve_session_quota (available=total<need), 必然预留失败。
        # 此时直接拒绝, 避免无效扩容 (Pod 创建后即成孤儿, 触发问题1的泄漏路径)。
        # service_concurrency 直接取自 deploy_template (模板配置或 sreq.service_template),
        # 缺失时回退到 Manager 全局 self._service_concurrency。
        sc_raw = deploy_template.get("service_concurrency") if deploy_template else None
        service_concurrency_for_tpl = (
            int(sc_raw) if sc_raw is not None else self._service_concurrency
        )
        if need > service_concurrency_for_tpl:
            logger.warning(
                "预检拦截(会话并发超过单实例总并发): session_id=%s template_id=%s "
                "session_concurrency=%s service_concurrency=%s, 拒绝扩容",
                session_id, target_template_id, need, service_concurrency_for_tpl,
            )
            return None

        h2 = await self._new_deployed(deploy_template)
        if h2 is None:
            return None
        self._in_use.setdefault(target_template_id, {})[h2.id] = h2
        logger.info(
            "新建实例并入 in_use: service_id=%s template_id=%s 当前该组总数=%s",
            h2.id, target_template_id, self._total_services_by_template(target_template_id)
        )
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
        # 错误响应必须符合上游 wire_codec.parse_agent_server_wire_chunk 的 legacy chunk shape
        # (request_id + channel_id + is_complete + payload, 且无 ok), 否则上游抛
        # ValueError: unrecognized wire shape, 导致前端收不到错误信息。
        # channel_id 直接取自 ISessionRequest.channel_id（与 AgentServer 的 request.channel_id 同源），
        # 不再通过 raw_msg 猜测形态（_SessionRequest.raw_msg 返回 JSON 字符串会取不到）。
        sreq = w.session_request
        rid = sreq.request_id or ""
        channel_id = sreq.channel_id
        await w.response_queue.put(
            {
                "request_id": rid,
                "channel_id": channel_id,
                "is_complete": True,
                "payload": {
                    "error_code": em.code,
                    "message": em.message,
                },
            }
        )
        if w.cancel and not w.cancel.done():
            w.cancel.set_result(None)

    def get_stats(self) -> dict:
        """返回当前 ServiceManager 的业务量统计（纯内存读取，无 IO）。"""
        in_use_count = sum(len(pool) for pool in self._in_use.values())
        idle_count = sum(len(pool) for pool in self._idle.values())
        total_inflight = sum(
            h.inflight_requests
            for pool in self._in_use.values()
            for h in pool.values()
        ) + sum(
            h.inflight_requests
            for pool in self._idle.values()
            for h in pool.values()
        )
        return {
            "user_queue_size": self._q.user_qsize(),
            "routing_tasks": len(self._user_route_tasks),
            "pods_in_use": in_use_count,
            "pods_idle": idle_count,
            "total_inflight_requests": total_inflight,
        }

    def mark_deprecated(self) -> None:
        """标记当前 ServiceManager 为待老化状态，并停止后台循环任务。"""
        self._deprecated = True
        logger.info("ServiceManager 已标记为待老化状态")
        
        # 立即停止 autoscale 和 pod monitor 循环，避免继续创建新实例
        if self._autoscale_task and not self._autoscale_task.done():
            self._autoscale_task.cancel()
            logger.debug("已取消 autoscale 任务")
        
        if self._pod_monitor_task and not self._pod_monitor_task.done():
            self._pod_monitor_task.cancel()
            logger.debug("已取消 pod_monitor 任务")

        if self._pod_watch_task and not self._pod_watch_task.done():
            self._pod_watch_task.cancel()
            logger.debug("已取消 pod_watch 任务")

    def is_deprecated(self) -> bool:
        """检查当前 ServiceManager 是否处于待老化状态。"""
        return self._deprecated

    async def try_cleanup_if_idle(self) -> bool:
        """尝试清理当前 ServiceManager（如果无在途任务和 inflight 请求）。
        
        对于已标记为 deprecated 的 ServiceManager，只要没有正在处理的请求
        （inflight_requests == 0），即使存在 session 元数据也可以安全清理，
        因为新请求都会路由到新的 ServiceManager。
        
        Returns:
            True 表示已清理成功或已经清理过，False 表示仍有活跃任务无法清理。
        """
        if not self._deprecated:
            logger.debug("ServiceManager 未标记为待老化，跳过清理检查")
            return False
        
        # 如果已经停止过，直接返回 True（幂等）
        if self._stop_completed:
            logger.debug("ServiceManager 已经停止过，跳过重复清理")
            return True
        
        # 检查是否有在途的用户路由任务
        active_tasks = [t for t in self._user_route_tasks if not t.done()]
        inflight_task_count = len(active_tasks)
        
        # 检查所有 service handler 的总 inflight 请求数（遍历所有模板组）
        total_inflight_requests = 0
        in_use_count = 0
        idle_count = 0

        for pool in self._in_use.values():
            in_use_count += len(pool)
            total_inflight_requests += sum(h.inflight_requests for h in pool.values())

        for pool in self._idle.values():
            idle_count += len(pool)
            total_inflight_requests += sum(h.inflight_requests for h in pool.values())
        
        # 如果没有在途任务且没有 inflight 请求，则可以安全清理
        # 注意：这里不检查 active_session_count，因为 session 只是元数据，
        # 只要没有正在处理的请求就可以安全清理
        if inflight_task_count == 0 and total_inflight_requests == 0:
            logger.info(
                "老化 ServiceManager 无在途任务和 inflight 请求，准备清理: in_use=%s idle=%s",
                in_use_count, idle_count,
            )
            try:
                await self.stop()
                return True
            except Exception as e:  # noqa: BLE001
                logger.error("老化 ServiceManager 停止失败: %s", e, exc_info=True)
                return False
        else:
            logger.debug(
                "老化 ServiceManager 仍有活跃任务，暂不清理: inflight_tasks=%s total_inflight_requests=%s",
                inflight_task_count, total_inflight_requests,
            )
            return False

    async def _pod_monitor_loop(self) -> None:
        """定期监测 Pod 状态并清理失效绑定。"""
        while self._running:
            try:
                await asyncio.sleep(self._pod_monitor_interval)
                if not self._running:
                    break
                await self._cleanup_failed_pods()
            except asyncio.CancelledError:
                logger.debug("Pod 监控任务被取消")
                break
            except (ApiException, config.ConfigException, OSError) as e:
                logger.error("调用 Pod 监控接口失败: %s", e, exc_info=True)
                #出错或断连后的“重试退避间隔”，防止异常时 CPU 空转和日志/请求风暴，同时保证循环能自愈继续跑。
                await asyncio.sleep(POD_WATCH_RECONNECT_DELAY_SECONDS)

    async def _pod_watch_loop(self) -> None:
        """监听 K8s Pod 事件，收到删除或失效事件后立即同步清理绑定关系。"""
        while self._running:
            pod_watch = None
            api_client = None
            try:
                await self._load_k8s_config()
                api_client = client.ApiClient()
                core = client.CoreV1Api(api_client)
                pod_watch = watch.Watch()
                logger.info(
                    "Pod Watch 已启动: namespace=%s selector=%s",
                    self._namespace,
                    POD_LABEL_SELECTOR,
                )
                async for event in pod_watch.stream(
                    core.list_namespaced_pod,
                    namespace=self._namespace,
                    label_selector=POD_LABEL_SELECTOR,
                    timeout_seconds=POD_WATCH_TIMEOUT_SECONDS,
                ):
                    if not self._running:
                        pod_watch.stop()
                        break
                    should_reconnect = await self._handle_pod_watch_event(event)
                    if should_reconnect:
                        pod_watch.stop()
                        break
            except asyncio.CancelledError:
                logger.debug("Pod Watch 任务被取消")
                if pod_watch is not None:
                    pod_watch.stop()
                break
            except (ApiException, config.ConfigException, OSError) as e:
                logger.error("Pod Watch 异常，将重连: %s", e, exc_info=True)
            finally:
                if pod_watch is not None:
                    pod_watch.stop()
                if api_client is not None:
                    await api_client.close()

            if self._running:
                await asyncio.sleep(POD_WATCH_RECONNECT_DELAY_SECONDS)

    async def _load_k8s_config(self) -> None:
        """加载 K8s 访问配置，供 Watch 长连接使用。"""
        try:
            config.load_incluster_config()
            logger.debug("K8s 已加载 in-cluster 配置（Watch）")
        except config.ConfigException:
            if self._kubeconfig:
                await config.load_kube_config(config_file=self._kubeconfig)
                logger.debug("K8s 已加载 kubeconfig: %s（Watch）", self._kubeconfig)
            else:
                await config.load_kube_config()
                logger.debug("K8s 已加载默认 kubeconfig（Watch）")

    async def _handle_pod_watch_event(self, event: Any) -> bool:
        """处理单条 Pod Watch 事件；返回 True 表示当前 Watch 需要重连。"""
        event_type = event.get("type") if isinstance(event, dict) else None
        if event_type == "ERROR":
            logger.warning("Pod Watch 收到 ERROR 事件，将重连: %s", event)
            return True

        pod = event.get("object") if isinstance(event, dict) else None
        metadata = getattr(pod, "metadata", None)
        pod_name = getattr(metadata, "name", None)
        if not pod_name:
            return False

        if event_type == "DELETED":
            await self._cleanup_dead_pods({pod_name: "Deleted"})
            return False

        if event_type != "MODIFIED":
            return False

        deletion_timestamp = getattr(metadata, "deletion_timestamp", None)
        deletion_timestamp_text = deletion_timestamp.isoformat() if deletion_timestamp else None
        phase = (getattr(getattr(pod, "status", None), "phase", None) or "Unknown")
        status, reason, _message, _restart_count = K8sServiceHandler.compute_pod_status(
            pod,
            phase,
            deletion_timestamp_text,
        )
        if deletion_timestamp_text or status in FAILED_POD_STATUSES:
            detail = f"{status}: {reason or ''}".strip()
            await self._cleanup_dead_pods({pod_name: detail})
        return False

    async def _cleanup_failed_pods(self) -> None:
        """检测失效 Pod 并清理绑定关系。"""
        # 1. 收集所有需要监控的 Pod
        pod_names_to_monitor = await self._collect_managed_pod_names()

        if not pod_names_to_monitor:
            logger.debug("Pod 监控：当前无 Pod 需要监控")
            return

        logger.debug("Pod 监控：开始检查 %s 个 Pod", len(pod_names_to_monitor))

        # 2. 调用监控接口
        try:
            pod_statuses = await K8sServiceHandler.monitor_pods_status(
                namespace=self._namespace,
                label_selector=POD_LABEL_SELECTOR,
                kubeconfig=self._kubeconfig,
            )
        except (ApiException, config.ConfigException, OSError) as e:
            logger.error("调用 Pod 监控接口失败: %s", e, exc_info=True)
            return

        # 3. 检测失效或已从 K8s list 中消失的 Pod 并清理
        failed_pods: Dict[str, str] = {}  # pod_name -> reason
        observed_pod_names = {pod.pod_name for pod in pod_statuses}
        for pod in pod_statuses:
            if pod.pod_name in pod_names_to_monitor and pod.status in FAILED_POD_STATUSES:
                failed_pods[pod.pod_name] = f"{pod.status}: {pod.reason or ''}"
        for pod_name in pod_names_to_monitor - observed_pod_names:
            failed_pods[pod_name] = "NotFound: pod absent from Kubernetes list"

        if not failed_pods:
            logger.debug("Pod 监控：所有 Pod 状态正常")
            return

        await self._cleanup_dead_pods(failed_pods)

    async def _collect_managed_pod_names(self) -> Set[str]:
        """收集当前 ServiceManager 池中仍记录的 Pod 名称。"""
        pod_names: Set[str] = set()
        async with self._lock:
            for pool in [self._in_use, self._idle]:
                for template_pool in pool.values():
                    for h in template_pool.values():
                        if hasattr(h, "pod_info") and h.pod_info:
                            pod_names.add(h.pod_info.pod_name)
        return pod_names

    def _remove_service_from_core_pools(self, service_id: str) -> tuple[bool, bool]:
        """从 in_use / idle 两个核心服务池移除指定 service_id。

        调用方应持有 self._lock，避免和路由、扩缩容同时移动同一个实例。
        返回值分别表示是否从 in_use、idle 中移除过记录。
        """
        removed_from_in_use = False
        removed_from_idle = False

        for template_pool in self._in_use.values():
            if template_pool.pop(service_id, None) is not None:
                removed_from_in_use = True

        for template_pool in self._idle.values():
            if template_pool.pop(service_id, None) is not None:
                removed_from_idle = True

        return removed_from_in_use, removed_from_idle

    async def _cleanup_dead_pods(self, dead_pods: Dict[str, str]) -> None:
        """按 Pod 名称清理服务池、session 路由和 handler 状态。"""
        if not dead_pods:
            return

        logger.debug("Pod 清理触发: candidates=%s", dead_pods)
        handlers_to_delete: list[tuple[str, IServiceHandler, str, str, list[str]]] = []

        async with self._lock:
            for pool in [self._in_use, self._idle]:
                for template_pool in pool.values():
                    for service_id, h in list(template_pool.items()):
                        if not hasattr(h, "pod_info") or not h.pod_info:
                            continue
                        pod_name = h.pod_info.pod_name
                        if pod_name not in dead_pods:
                            continue

                        # 去重：watch/轮询/缩容/stop 可能并发命中同一实例，已在清理中的直接跳过
                        if service_id in self._deleting_services:
                            continue
                        self._deleting_services.add(service_id)

                        reason = dead_pods[pod_name]
                        logger.error(
                            "Pod 已失效，移除 Service: service_id=%s pod_name=%s reason=%s",
                            service_id,
                            pod_name,
                            reason,
                        )

                        removed_from_in_use, removed_from_idle = self._remove_service_from_core_pools(service_id)
                        logger.info(
                            "已从服务池移除失效 Service: service_id=%s in_use=%s idle=%s",
                            service_id,
                            removed_from_in_use,
                            removed_from_idle,
                        )
                        self._to_idle_timer_armed.discard(service_id)
                        self._excess_idle_timer_armed.discard(service_id)

                        router_session_ids = await self._service_router.delete_service_sessions(service_id)
                        if router_session_ids:
                            logger.info(
                                "已清理失效 Service 的 session 亲和映射: service_id=%s sessions=%s",
                                service_id,
                                router_session_ids,
                            )
                        session_ids = sorted(set(h.open_session_ids()) | set(router_session_ids))
                        for session_id in session_ids:
                            self._pending_expired_sessions.pop(session_id, None)
                            try:
                                await h.remove_session(session_id)
                            except Exception as e:
                                logger.error(
                                    "移除失效 Pod 的 Session 失败: service_id=%s session_id=%s err=%s",
                                    service_id,
                                    session_id,
                                    e,
                                    exc_info=True,
                                )
                            logger.info(
                                "已移除失效 Pod 的 Session 绑定: session_id=%s service_id=%s",
                                session_id,
                                service_id,
                            )

                        handlers_to_delete.append((service_id, h, pod_name, reason, session_ids))

        for service_id, h, pod_name, reason, session_ids in handlers_to_delete:
            try:
                for session_id in session_ids:
                    await self._timer.cancel_timer(f"sess:{session_id}")
                await self._cancel_in_use_to_idle_timer(service_id)
                await self._cancel_excess_idle_timer(service_id)
                try:
                    await h.delete()
                except (ApiException, config.ConfigException, OSError, RuntimeError) as e:
                    logger.error(
                        "清理失效 Pod 失败: service_id=%s pod_name=%s reason=%s err=%s",
                        service_id,
                        pod_name,
                        reason,
                        e,
                        exc_info=True,
                    )
            finally:
                self._deleting_services.discard(service_id)

        if handlers_to_delete:
            logger.warning("Pod 监控：清理完成，已移除 %s 个失效 Pod", len(handlers_to_delete))
