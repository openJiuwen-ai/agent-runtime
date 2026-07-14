# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务管理（Pod 池）：双 asyncio 队列（系统优先）、Pod 生命周期、autoscale、亲和借还、起停与缩容。

解耦后 session 编排（消息入口、TTL、pending 过期）外移到 SessionRuntimeManager，
本类仅保留 Pod 池职责。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set, Union
from kubernetes_asyncio import client, config, watch
from kubernetes_asyncio.client.rest import ApiException

from openjiuwen_runtime.foundation.log import get_logger

from .dual_queue import PriorityDualAsyncQueues
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
from .session_runtime_manager import SessionRuntimeManager

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
        self._running = False
        self._message_task: Optional[asyncio.Task[Any]] = None
        self._autoscale_task: Optional[asyncio.Task[Any]] = None
        self._pod_monitor_task: Optional[asyncio.Task[Any]] = None
        self._pod_watch_task: Optional[asyncio.Task[Any]] = None
        # 正在被失效 Pod 清理流程删除的 service_id，避免 watch/轮询/缩容/stop 之间重复 delete
        self._deleting_services: set[str] = set()
        # 缩容已从池摘走、K8s delete 尚未结束：仍计入 max_services，避免第二轮抢跑占位
        self._reclaim_occupancy: dict[Optional[str], int] = {}
        self._user_route_tasks: set[asyncio.Task[Any]] = set()
        # 缩容回收（含 await delete）后台任务；不可 await 在 message_loop 内，否则堵住用户出队
        self._reclaim_tasks: set[asyncio.Task[Any]] = set()
        # 已 arm「in_use → idle」计时的 service_id，避免对同一实例重复开多个计时器
        self._to_idle_timer_armed: set[str] = set()
        # 已 arm「多余 idle 回收」的 service_id，避免对同一台重复入队/定时
        self._excess_idle_timer_armed: set[str] = set()
        # session 编排（消息入口 / TTL / pending 过期）——由 Access 在构造后注入，
        # 避免 ServiceManager ↔ SessionRuntimeManager 构造期的循环依赖
        self._session_runtime: Optional["SessionRuntimeManager"] = None
        # deploy 进行中、尚未入 _idle/_in_use 的实例（stop 时必须一并 delete）
        self._deploying: set[IServiceHandler] = set()
        # 已占位、尚未入池的 deploy 计数（按 template_id），计入 max_services，避免锁外并行冷启动超限
        self._pending_deploys: dict[Optional[str], int] = {}
        # 目标入 idle 的 pending（预热/autoscale）；计入 min_idle，避免锁外 deploy 期间被再补一台
        self._pending_idle_deploys: dict[Optional[str], int] = {}
        # 同 session_id 冷启动合并：leader 占 pending 并 deploy，后来者 await 同一 Future 再选池/绑定
        self._session_deploy_waiters: dict[
            str, asyncio.Future[Optional[IServiceHandler]]
        ] = {}
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

    def set_session_runtime(self, session_runtime: "SessionRuntimeManager") -> None:
        """注入 session 编排层（由 Access 在构造两者后调用，打破构造期循环依赖）。"""
        self._session_runtime = session_runtime

    # ==================== 供 SessionRuntimeManager 调用的 Pod 池接口 ====================

    async def pick_or_create_pod(self, sreq) -> Optional[IServiceHandler]:  # noqa: ANN001
        """选/建一个 Pod 供 session 装填为 endpoint；返回前取消其 idle/reclaim 计时。"""
        h = await self._pick_or_create(sreq)
        if h is not None:
            # 该 Pod 即将承载业务：取消 in_use→idle 与 多余 idle 回收 计时
            await self._cancel_in_use_to_idle_timer(h.id)
            await self._cancel_excess_idle_timer(h.id)
        return h

    def find_service_handler(self, service_id: str) -> Optional[IServiceHandler]:
        """公开包装：在所有模板组的 in_use/idle 池中查找 service_id。"""
        return self._find_service_handler(service_id)

    async def start(self) -> None:
        if self._running:
            logger.debug("ServiceManager start 被忽略: 已在运行中")
            return
        self._running = True

        # 若未由上层注入 session 编排层，则在此自动构造（兼容直接构造 ServiceManager 的用法）
        if self._session_runtime is None:
            self._session_runtime = SessionRuntimeManager(self._timer, self)

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
        """停止 ServiceManager（幂等）。

        依次取消并等待：消息循环、autoscale 循环、Pod 监控/watch（若启用）、
        在途用户路由任务、在途缩容回收任务；再取消全部计时器，
        delete 所有 in_use/idle/deploying 实例，并清理 session 编排层。
        """
        # 使用锁防止并发调用 stop()
        async with self._stop_lock:
            if self._stop_completed:
                logger.debug("ServiceManager stop 被忽略: 已停止")
                return
            
            self._running = False
            self._q.mark_closed()
            logger.info(
                "ServiceManager 正在停止: 已标记队列关闭, 在途用户路由=%s 在途缩容=%s",
                len(self._user_route_tasks),
                len(self._reclaim_tasks),
            )
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
            # 缩容已从 idle pop，cancel 会导致 K8s delete 半截中断 → 幽灵 Pod。
            # 必须等 reclaim 跑完（其内部对 delete 使用 shield）。
            if self._reclaim_tasks:
                logger.info(
                    "ServiceManager 停止: 等待 %s 个在途缩容完成",
                    len(self._reclaim_tasks),
                )
                await asyncio.gather(
                    *self._reclaim_tasks, return_exceptions=True
                )
            self._reclaim_tasks.clear()
            self._to_idle_timer_armed.clear()
            self._excess_idle_timer_armed.clear()
            self._deleting_services.clear()
            self._reclaim_occupancy.clear()
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
                # 标记为本轮 stop 认领，避免锁外 deploy 完成后的 orphan 路径二次 delete
                for h in all_handlers:
                    self._deleting_services.add(h.id)
                self._in_use.clear()
                self._idle.clear()
                self._deploying.clear()
                self._pending_deploys.clear()
                self._pending_idle_deploys.clear()
                for fut in self._session_deploy_waiters.values():
                    if not fut.done():
                        fut.set_result(None)
                self._session_deploy_waiters.clear()
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

            # 清理 session 编排层（TTL 计时 / pending 标记）
            if self._session_runtime is not None:
                try:
                    await self._session_runtime.shutdown()
                except Exception as e:  # noqa: BLE001
                    logger.error("SessionRuntimeManager shutdown 失败: %s", e, exc_info=True)

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
            # stop_completed 置位后再摘认领标记，避免与 orphan 路径夹缝二次 delete
            for h in all_handlers:
                self._deleting_services.discard(h.id)

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
        """获取所有模板组的总实例数（含 pending deploy / 缩容占位）。"""
        total = 0
        for pool in self._in_use.values():
            total += len(pool)
        for pool in self._idle.values():
            total += len(pool)
        total += sum(self._pending_deploys.values())
        total += sum(self._reclaim_occupancy.values())
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
        """获取指定 template_id 的总实例数（in_use + idle + pending deploy + 缩容占位）。"""
        in_use_count = len(self._in_use.get(template_id, {}))
        idle_count = len(self._idle.get(template_id, {}))
        pending = self._pending_deploys.get(template_id, 0)
        reclaiming = self._reclaim_occupancy.get(template_id, 0)
        return in_use_count + idle_count + pending + reclaiming

    def _begin_pending_deploy_locked(
        self, template_id: Optional[str], *, into: str = "in_use"
    ) -> None:
        """调用方须持有 self._lock。``into`` 为 idle / in_use。"""
        self._pending_deploys[template_id] = self._pending_deploys.get(template_id, 0) + 1
        if into == "idle":
            self._pending_idle_deploys[template_id] = (
                self._pending_idle_deploys.get(template_id, 0) + 1
            )

    def _end_pending_deploy_locked(
        self, template_id: Optional[str], *, into: str = "in_use"
    ) -> None:
        """调用方须持有 self._lock。"""
        n = self._pending_deploys.get(template_id, 0)
        if n <= 1:
            self._pending_deploys.pop(template_id, None)
        else:
            self._pending_deploys[template_id] = n - 1
        if into == "idle":
            ni = self._pending_idle_deploys.get(template_id, 0)
            if ni <= 1:
                self._pending_idle_deploys.pop(template_id, None)
            else:
                self._pending_idle_deploys[template_id] = ni - 1

    def _idle_count_by_template(self, template_id: Optional[str]) -> int:
        """获取指定 template_id 的空闲实例数（不含尚未入池的 pending）。"""
        return len(self._idle.get(template_id, {}))

    def _effective_idle_count_by_template(self, template_id: Optional[str]) -> int:
        """idle + 即将入 idle 的 pending，用于 min_idle 判定。"""
        return (
            self._idle_count_by_template(template_id)
            + self._pending_idle_deploys.get(template_id, 0)
        )

    def _discard_user_route_task(self, task: asyncio.Task[Any]) -> None:
        self._user_route_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("用户路由子任务失败: %s", exc, exc_info=True)

    def _discard_reclaim_task(self, task: asyncio.Task[Any]) -> None:
        self._reclaim_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("缩容回收子任务失败: %s", exc, exc_info=True)

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
                # 缩容含 await delete，必须后台执行；串行 await 会让 message_loop
                # 无法继续取出用户请求（即便 user 队列已有积压）。
                logger.info("处理系统事件: 缩容回收 service_id=%s (后台)", item.service_id)
                t = asyncio.create_task(self._on_service_reclaim(item.service_id))
                self._reclaim_tasks.add(t)
                t.add_done_callback(self._discard_reclaim_task)
                continue
            if not isinstance(item, RawMessage):
                logger.debug("跳过非 RawMessage: %s", type(item))
                continue
            if item.message_type == MessageType.USER_REQUEST:
                # 为每条用户消息创建独立协程，使多 session 可并行进入 ServiceHandler
                # session 编排（TTL/亲和/pending）全部在 SessionRuntimeManager 内
                rt = self._session_runtime
                if rt is None:
                    logger.error("SessionRuntimeManager 未注入, 丢弃用户消息")
                    continue
                t = asyncio.create_task(rt.handle_user_request(item))
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

    async def _fill_min_idle_for_template(
        self,
        template_id: Optional[str],
        *,
        min_idle_services: int,
        max_services: int,
        deploy_template: Optional[Dict[str, Any]],
        log_prefix: str,
    ) -> None:
        """按需补齐 idle；锁内只占位，deploy 在锁外执行，避免堵住用户路由。"""
        if min_idle_services <= 0:
            return
        first_gap = True
        while True:
            async with self._lock:
                # 须把 pending_idle 算进 min_idle：锁外 deploy 期间 autoscale 也会进来，
                # 若只看已入池 idle，会在预热未完成时再补拉一台。
                idle_n = self._effective_idle_count_by_template(template_id)
                total_n = self._total_services_by_template(template_id)
                if idle_n >= min_idle_services or total_n >= max_services:
                    return
                if first_gap:
                    first_gap = False
                    logger.debug(
                        "%s: min_idle=%s 但当前 effective_idle=%s (pool_idle=%s total=%s), 将补发新实例",
                        log_prefix,
                        min_idle_services,
                        idle_n,
                        self._idle_count_by_template(template_id),
                        total_n,
                    )
                self._begin_pending_deploy_locked(template_id, into="idle")

            h = await self._deploy_and_admit(
                template_id, deploy_template, into="idle"
            )
            if h is None:
                logger.error(
                    "%s失败 (template_id=%s): factory/deploy 未返回可用实例, 已停止继续拉起",
                    log_prefix,
                    template_id,
                )
                return
            logger.info(
                "%s: 新实例入 idle (template_id=%s), service_id=%s",
                log_prefix,
                template_id,
                h.id,
            )

    async def _bootstrap_min_idle(self) -> None:
        if self._min_idle <= 0:
            return

        # 如果没有配置模板，使用默认配置拉起
        if not self._service_templates:
            await self._fill_min_idle_for_template(
                None,
                min_idle_services=self._min_idle,
                max_services=self._max_services,
                deploy_template=None,
                log_prefix="预拉热",
            )
            return

        # 按每个模板配置分别拉起
        for tpl in self._service_templates:
            template_id = tpl.get("template_id")
            min_idle_services = tpl.get("min_idle_services", self._min_idle)
            max_services = tpl.get("max_services", self._max_services)
            await self._fill_min_idle_for_template(
                template_id,
                min_idle_services=min_idle_services,
                max_services=max_services,
                deploy_template=tpl,
                log_prefix="预拉热",
            )

        # 预拉热入 idle 的实例不启动「in_use→idle / 删 Pod」的 service_ttl 计时

    async def _ensure_min_idle(self) -> None:
        if self._min_idle <= 0:
            return

        # 如果没有配置模板，使用默认配置
        if not self._service_templates:
            await self._fill_min_idle_for_template(
                None,
                min_idle_services=self._min_idle,
                max_services=self._max_services,
                deploy_template=None,
                log_prefix="autoscale",
            )
            return

        # 按每个模板配置分别维护
        for tpl in self._service_templates:
            template_id = tpl.get("template_id")
            min_idle_services = tpl.get("min_idle_services", self._min_idle)
            max_services = tpl.get("max_services", self._max_services)
            await self._fill_min_idle_for_template(
                template_id,
                min_idle_services=min_idle_services,
                max_services=max_services,
                deploy_template=tpl,
                log_prefix=f"autoscale (template_id={template_id})",
            )
        # 新入 idle 的实例不启动 service_ttl 删 Pod；仅 min_idle 维持数量

    async def _safe_delete_handler(self, h: IServiceHandler) -> None:
        """幂等 delete：若 stop/监控等已认领同一实例，则跳过，避免二次 delete。"""
        async with self._lock:
            if h.id in self._deleting_services:
                logger.info(
                    "跳过 delete: 实例已由其他清理路径处理 service_id=%s",
                    h.id,
                )
                return
            self._deleting_services.add(h.id)
        try:
            await h.delete()
        except Exception as e:  # noqa: BLE001
            logger.error(
                "清理服务实例失败: service_id=%s err=%s", h.id, e, exc_info=True
            )
        finally:
            async with self._lock:
                self._deleting_services.discard(h.id)

    def _evacuate_same_id_locked(
        self,
        service_id: str,
        *,
        keep: IServiceHandler,
    ) -> Optional[IServiceHandler]:
        """池中挤出同 service_id 的旧实例（保留 ``keep``）。调用方须持有 ``self._lock``。

        典型场景：service_id 由 group+bot 等确定性命名，二次 cold start 得到新 handler，
        若直接 ``pool[id]=new`` 会静默覆盖旧 Pod，需显式挤出并 delete。
        """
        displaced: Optional[IServiceHandler] = None
        for pool_map in (self._in_use, self._idle):
            for tid, pool in pool_map.items():
                existing = pool.get(service_id)
                if existing is None or existing is keep:
                    continue
                pool.pop(service_id, None)
                displaced = existing
                logger.warning(
                    "同 service_id 实例被挤出: service_id=%s template_id=%s "
                    "(将 delete 旧 handler，避免 K8s Pod 孤儿残留)",
                    service_id,
                    tid,
                )
                break
            if displaced is not None:
                break
        if displaced is not None:
            self._to_idle_timer_armed.discard(service_id)
            self._excess_idle_timer_armed.discard(service_id)
        return displaced

    async def _cleanup_displaced_handler(self, old: IServiceHandler) -> None:
        """清理被同 id 挤出的旧 handler，并 delete 其底层资源。"""
        await self._cancel_in_use_to_idle_timer(old.id)
        await self._cancel_excess_idle_timer(old.id)
        # session 侧：从引用该 Pod 的 SessionHandler 摘除 endpoint、清 pending
        if self._session_runtime is not None:
            try:
                await self._session_runtime.on_pod_removed(old.id)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "挤出实例的 session 侧清理失败: service_id=%s err=%s",
                    old.id, e, exc_info=True,
                )
        for session_id in list(old.open_session_ids()):
            await self._timer.cancel_timer(f"sess:{session_id}")
        try:
            # 不用 _safe_delete_handler：勿把 service_id 放进 _deleting_services，
            # 否则会与新实例的后续清理路径互相干扰。
            await asyncio.shield(old.delete())
            logger.warning(
                "已 delete 被挤出的旧实例: service_id=%s",
                old.id,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "delete 被挤出实例失败: service_id=%s err=%s",
                old.id,
                e,
                exc_info=True,
            )

    async def _deploy_and_admit(
        self,
        template_id: Optional[str],
        deploy_template: Optional[Dict[str, Any]],
        *,
        into: str,
    ) -> Optional[IServiceHandler]:
        """调用方须已 ``_begin_pending_deploy_locked`` 且已释放锁。deploy 在锁外执行。"""
        h: Optional[IServiceHandler] = None
        orphan: Optional[IServiceHandler] = None
        displaced: Optional[IServiceHandler] = None
        admitted: Optional[IServiceHandler] = None
        try:
            h = await self._new_deployed(deploy_template)
        finally:
            async with self._lock:
                self._end_pending_deploy_locked(template_id, into=into)
                if h is None:
                    pass
                elif not self._running:
                    # stop() 可能已从 _deploying 摘走并认领 delete；勿二次 orphan delete
                    if self._stop_completed or h.id in self._deleting_services:
                        logger.info(
                            "deploy 完成时 stop 已认领/已完成清理, 跳过 orphan delete: "
                            "service_id=%s template_id=%s stop_completed=%s",
                            h.id,
                            template_id,
                            self._stop_completed,
                        )
                    else:
                        orphan = h
                        logger.info(
                            "deploy 完成但 ServiceManager 已停止, 将 orphan delete: "
                            "service_id=%s template_id=%s into=%s",
                            h.id,
                            template_id,
                            into,
                        )
                elif into == "idle":
                    displaced = self._evacuate_same_id_locked(h.id, keep=h)
                    self._idle.setdefault(template_id, {})[h.id] = h
                    admitted = h
                else:
                    displaced = self._evacuate_same_id_locked(h.id, keep=h)
                    self._in_use.setdefault(template_id, {})[h.id] = h
                    admitted = h
                    logger.info(
                        "新建实例并入 in_use: service_id=%s template_id=%s 当前该组总数=%s",
                        h.id,
                        template_id,
                        self._total_services_by_template(template_id),
                    )
        if orphan is not None:
            await self._safe_delete_handler(orphan)
            return None
        if displaced is not None:
            await self._cleanup_displaced_handler(displaced)
        return admitted

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
        async with self._lock:
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
            async with self._lock:
                self._deploying.discard(h)
        logger.debug("新服务 deploy 成功, 待加入池: service_id=%s", h.id)
        return h

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

    async def _on_in_use_may_move_to_idle_pool(self, service_id: str) -> None:
        """ServiceHandler 在 inflight=0 时回调：先推动 session 层 flush pending 过期，再按状态 arm service_ttl。"""
        await self.reconsider_idle_transition(service_id)

    async def reconsider_idle_transition(self, service_id: str) -> None:
        """供 session 层在 evict_session 释放 quota 后调用，重新评估该 Pod 的 idle 转换。

        场景：session TTL 到期 evict 释放 quota 时，inflight 往往早已归零，
        inflight 归零 hook 不会再触发；需由 session 层主动调用本方法推动
        in_use→idle 的 service_ttl 计时，否则工作 Pod 会卡在 in_use 池。
        """
        # 1. session 层：清理该 Pod 上「到期但仍 inflight（现已归零）」的 session
        if self._session_runtime is not None:
            try:
                await self._session_runtime.flush_pending_for_service(service_id)
            except Exception as e:  # noqa: BLE001
                logger.error("flush_pending_for_service 失败: service_id=%s err=%s", service_id, e, exc_info=True)
        # 2. Pod 层：按 Pod 状态 arm service_ttl（转入 idle）
        h = self._find_service_handler(service_id)
        if h is None:
            return
        in_in_use = False
        async with self._lock:
            for pool in self._in_use.values():
                if service_id in pool:
                    in_in_use = True
                    break
        if in_in_use:
            await self._arm_in_use_to_idle_pool(service_id)

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
        """从 idle 摘实例并 delete；锁外 delete，且不受外层 cancel 中断。"""
        self._excess_idle_timer_armed.discard(service_id)
        h: Optional[IServiceHandler] = None
        should_delete = False
        template_id: Optional[str] = None

        async with self._lock:
            if service_id in self._deleting_services:
                logger.debug(
                    "缩容跳过: 实例已在删除中 service_id=%s", service_id
                )
                return

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
            self._deleting_services.add(service_id)
            self._reclaim_occupancy[template_id] = (
                self._reclaim_occupancy.get(template_id, 0) + 1
            )
            h = oh
            should_delete = True
        if not should_delete or h is None:
            return
        try:
            # pop 后必须尽量把 delete 做完：外层 task cancel / stop 不得半截打断
            await asyncio.shield(h.delete())
            logger.info(
                "缩容已删除 idle 实例: service_id=%s template_id=%s (多余或系统事件)",
                service_id, template_id
            )
        except Exception as e:  # noqa: BLE001
            logger.error("缩容 delete 失败: service_id=%s err=%s", service_id, e, exc_info=True)
            return
        finally:
            async with self._lock:
                self._deleting_services.discard(service_id)
                n = self._reclaim_occupancy.get(template_id, 0)
                if n <= 1:
                    self._reclaim_occupancy.pop(template_id, None)
                else:
                    self._reclaim_occupancy[template_id] = n - 1
        await self._schedule_excess_idle_reclaim_if_needed()

    def _pick_existing_locked(
        self, need: int, template_id: Optional[str]
    ) -> Optional[IServiceHandler]:
        """在池中按容量选实例。调用方须持有 self._lock。不 deploy。

        注：session 亲和由 SessionHandler / SessionRuntimeManager 处理，此处不做亲和查找。
        """
        in_use_pool = self._in_use.get(template_id, {})
        for h in in_use_pool.values():
            if h.available_concurrency >= need:
                logger.debug(
                    "选用 in_use 实例 (template_id=%s): service_id=%s avail=%s",
                    template_id, h.id, h.available_concurrency,
                )
                return h

        idle_pool = self._idle.get(template_id, {})
        for h in list(idle_pool.values()):
            if h.available_concurrency >= need:
                idle_pool.pop(h.id, None)
                self._in_use.setdefault(template_id, {})[h.id] = h
                logger.debug(
                    "从 idle 唤醒实例 (template_id=%s): service_id=%s",
                    template_id, h.id,
                )
                return h
        return None

    def _try_begin_deploy_locked(
        self, sreq, need: int, template_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        """锁内判断是否可扩容并占位。成功返回 deploy_template，否则 None。"""
        template_config = self._get_template_config(template_id)
        max_services_for_tpl = self._get_template_max_services(template_id)

        if self._total_services_by_template(template_id) >= max_services_for_tpl:
            logger.debug(
                "pick: 未选到可用实例且 template_id=%s 已达 max_services=%s, 当前该组实例=%s",
                template_id, max_services_for_tpl,
                self._total_services_by_template(template_id),
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
                sreq.session_id, template_id, need, service_concurrency_for_tpl,
            )
            return None

        self._begin_pending_deploy_locked(template_id, into="in_use")
        return deploy_template if deploy_template is not None else {}

    def _get_session_deploy_waiter_locked(
        self, session_id: str
    ) -> Optional[asyncio.Future[Optional[IServiceHandler]]]:
        """若该 session 已有进行中的冷启动，返回其 Future（调用方须持锁）。"""
        fut = self._session_deploy_waiters.get(session_id)
        if fut is None or fut.done():
            return None
        return fut

    def _register_session_deploy_leader_locked(
        self, session_id: str
    ) -> asyncio.Future[Optional[IServiceHandler]]:
        """当前协程成为该 session 冷启动 leader；调用方须已占 pending 且持锁。"""
        fut: asyncio.Future[Optional[IServiceHandler]] = (
            asyncio.get_running_loop().create_future()
        )
        self._session_deploy_waiters[session_id] = fut
        return fut

    async def _pick_or_create(self, sreq) -> Optional[IServiceHandler]:  # noqa: ANN001
        """为 session 请求选择或创建一个**有可用容量**的 Pod 实例（endpoint 装填用）。

        选择策略：
        1) 在相同 template_id 组的 in_use 池中找尚有服务级并发的实例
        2) 否则在 idle 池中唤醒一个有容量的实例
        3) 再否则在该 template_id 组的 max 允许下新 deploy（deploy 在锁外执行）
           同一 session_id 仅允许一个 in-flight deploy；后来者 await 其结果再选池，
           避免惊群重复占满 max_services。
           缩容 delete 未完成前仍占用 max 名额（reclaim_occupancy）。

        注：session 亲和（同 session 复用同一 Pod）由 SessionHandler（持有 endpoints）
        在 SessionRuntimeManager 层处理，本方法不再做亲和查找 / 额度预留。

        注意：本方法自行获取/释放 ``self._lock``，调用方勿再持锁调用。
        """
        need = max(1, int(sreq.session_concurrency))
        session_id = sreq.session_id
        template_id: Optional[str] = None
        if sreq.service_template:
            template_id = sreq.service_template.get("template_id")

        # 冷启动占位后偶发竞态，允许有限次重试选池
        for _ in range(5):
            deploy_template: Optional[Dict[str, Any]] = None
            join_future: Optional[asyncio.Future[Optional[IServiceHandler]]] = None
            leader_future: Optional[asyncio.Future[Optional[IServiceHandler]]] = None
            async with self._lock:
                h = self._pick_existing_locked(need, template_id)
                if h is not None:
                    return h
                join_future = self._get_session_deploy_waiter_locked(session_id)
                if join_future is None:
                    deploy_template = self._try_begin_deploy_locked(
                        sreq, need, template_id
                    )
                    if deploy_template is not None:
                        leader_future = self._register_session_deploy_leader_locked(
                            session_id
                        )

            if join_future is not None:
                try:
                    await join_future
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "等待同 session 冷启动结束异常, session_id=%s",
                        session_id,
                        exc_info=True,
                    )
                # leader 已 admit（或失败）；本轮重新选池，不再占 pending
                continue

            if deploy_template is None:
                return None

            admitted: Optional[IServiceHandler] = None
            try:
                admitted = await self._deploy_and_admit(
                    template_id, deploy_template or None, into="in_use"
                )
            finally:
                async with self._lock:
                    # 仅当仍是本 leader 注册的 Future 时收尾，避免误伤后续重试
                    if (
                        leader_future is not None
                        and self._session_deploy_waiters.get(session_id)
                        is leader_future
                    ):
                        self._session_deploy_waiters.pop(session_id, None)
                    if leader_future is not None and not leader_future.done():
                        leader_future.set_result(admitted)
            if admitted is None:
                return None
            # 入池后下一轮再选池（额度预留由 SessionRuntimeManager 完成）

        return None

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

    @staticmethod
    def _handler_pod_names(h: IServiceHandler) -> Set[str]:
        """收集 handler 已关联的 Pod 名（含 deploy 中 resource_id 尚未回填 pod_info 的情况）。"""
        names: Set[str] = set()
        if hasattr(h, "pod_info") and h.pod_info and getattr(h.pod_info, "pod_name", None):
            names.add(str(h.pod_info.pod_name))
        deploy = getattr(h, "_deploy", None)
        rid = getattr(deploy, "resource_id", None) if deploy is not None else None
        if rid:
            names.add(str(rid))
        return names

    async def _collect_managed_pod_names(self) -> Set[str]:
        """收集当前 ServiceManager 池中仍记录的 Pod 名称（含 deploy 进行中）。"""
        pod_names: Set[str] = set()
        async with self._lock:
            for pool in [self._in_use, self._idle]:
                for template_pool in pool.values():
                    for h in template_pool.values():
                        pod_names |= self._handler_pod_names(h)
            for h in self._deploying:
                pod_names |= self._handler_pod_names(h)
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

                        # session 侧清理（委托 session 编排层）：从引用该 Pod 的
                        # SessionHandler 摘除 endpoint、清 _pending_expired 记录。
                        # 注：ServiceManager 不再持有 _service_router / _pending_expired_sessions
                        # （已迁至 SessionRuntimeManager）。
                        if self._session_runtime is not None:
                            try:
                                await self._session_runtime.on_pod_removed(service_id)
                            except Exception as e:  # noqa: BLE001
                                logger.error(
                                    "session 侧清理失效 Pod 失败: service_id=%s err=%s",
                                    service_id, e, exc_info=True,
                                )
                        session_ids = list(h.open_session_ids())

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