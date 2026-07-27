# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Session 运行时管理：ServiceScopeHandler 归属、消息入口、chat_session TTL/亲和/pending 过期。

第二层解耦：从 ServiceManager 抽取的 session 编排职责。与 ServiceManager（Pod 池）协作：
- 向 ServiceManager 借/还 endpoint（Pod）；
- ServiceManager 在 Pod inflight 归零时回调 :meth:`flush_pending_for_service`。

内含 :class:`ServiceScopeRegistry`（ServiceScopeHandler 归属，替代原 ServiceHandler._sessions）。

并发位（semaphore，= scope_concurrency）由本层 ``scope_handler.acquire_slot() / release_slot()``
驱动（绑定级 chat_session 闸门：新 session 首次绑定时 acquire，unbind/remove_endpoint 时 release）。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from openjiuwen_runtime.foundation.log import get_logger

from .exception import exception_message
from .interfaces import (
    IServiceHandler,
    ITimer,
    RawMessage,
    ScopeRequestWrapper,
)
from .router import SessionRouter
from .service_scope_handler import ServiceScopeHandler

if TYPE_CHECKING:
    # 仅用于类型提示，避免与 ServiceManager 的运行时循环导入
    from .service_manager import ServiceManager

logger = get_logger(__name__)


class ServiceScopeRegistry:
    """ServiceScopeHandler 的归属与生命周期管理（替代原 ServiceHandler._sessions）。

    每个 scope_id（= service_id）对应一个 ServiceScopeHandler；endpoints 由 SessionRuntimeManager 装填。
    """

    def __init__(self, session_router: SessionRouter) -> None:
        self._lock = asyncio.Lock()
        self._handlers: Dict[str, ServiceScopeHandler] = {}
        self._session_router = session_router

    @property
    def session_router(self) -> SessionRouter:
        return self._session_router

    async def get_or_create(
        self, scope_id: str, max_parallel: int, reserve_per_pod: int,
    ) -> ServiceScopeHandler:
        """获取或创建 ServiceScopeHandler（endpoints 为空，由 SessionRuntimeManager 装填）。"""
        async with self._lock:
            sh = self._handlers.get(scope_id)
            if sh is None:
                sh = ServiceScopeHandler(
                    scope_id, max_parallel, [], self._session_router, reserve_per_pod
                )
                self._handlers[scope_id] = sh
                logger.info(
                    "ServiceScopeRegistry 新建 ServiceScopeHandler: scope_id=%s "
                    "max_parallel=%s reserve_per_pod=%s",
                    scope_id, max_parallel, reserve_per_pod,
                )
            return sh

    def get(self, scope_id: str) -> Optional[ServiceScopeHandler]:
        return self._handlers.get(scope_id)

    def has_session(self, scope_id: str) -> bool:
        return scope_id in self._handlers

    async def remove(self, scope_id: str) -> Optional[ServiceScopeHandler]:
        async with self._lock:
            return self._handlers.pop(scope_id, None)

    def active_count(self) -> int:
        return len(self._handlers)

    def all_session_ids(self) -> List[str]:
        return list(self._handlers.keys())


class SessionRuntimeManager:
    """Session 生命周期与编排（从 ServiceManager 抽取）。

    职责：消息入口、chat_session TTL、pending 过期清理、向 Pod 池借/还 endpoint。
    """

    def __init__(
        self,
        timer: ITimer,
        service_manager: "ServiceManager",
    ) -> None:
        self._timer = timer
        self._sm = service_manager
        self._registry = ServiceScopeRegistry(SessionRouter())
        self._lock = asyncio.Lock()
        # chat_session_id -> (scope_id, endpoint_id)：TTL 到期时仍有 inflight 的延期清理队列
        self._pending_chat_expiry: Dict[str, Tuple[str, Optional[str]]] = {}

    @property
    def registry(self) -> ServiceScopeRegistry:
        return self._registry

    # ==================== 消息入口 ====================

    async def handle_user_request(self, raw: RawMessage) -> None:
        w = raw.message
        if not isinstance(w, ScopeRequestWrapper):
            logger.error("用户消息体类型错误, 预期 ScopeRequestWrapper, 实际 %s", type(w))
            return
        sreq = w.session_request
        scope_id = sreq.service_id

        if self._sm.is_deprecated():
            logger.warning(
                "待老化的 ServiceManager 正在处理新请求: scope_id=%s request_id=%s",
                scope_id,
                sreq.request_id,
            )

        # chat_session 标识（渠道无关；page session 或 IM 聊天会话）。
        # 优先从 ISessionRequest.session_id 获取（实现层正确传递），回退到 raw_msg 解析。
        chat_session_id: Optional[str] = None
        if hasattr(sreq, "session_id") and sreq.session_id:
            chat_session_id = sreq.session_id
        else:
            raw_msg = sreq.raw_msg
            if raw_msg is not None:
                chat_session_id = getattr(raw_msg, "session_id", None) or getattr(
                    raw_msg, "user_id", None
                )

        # 新消息进入：先取消该 chat_session 待生效的 TTL 计时与延期清理标记
        if chat_session_id:
            await self._timer.cancel_timer(f"csess:{chat_session_id}")
            self._pending_chat_expiry.pop(chat_session_id, None)

        scope_handler: Optional[ServiceScopeHandler] = None
        failed_to_reserve: Optional[IServiceHandler] = None
        _acquired = False  # 是否为本请求占用的 semaphore 槽位（cancel/异常时据此归还）
        try:
            reserve_per_pod = self._sm.reserve_per_pod_for(sreq)
            scope_handler = await self._registry.get_or_create(
                scope_id, sreq.session_concurrency, reserve_per_pod
            )

            # chat_session 并发槽位闸门：仅未绑定的新 chat_session 才 acquire（满则排队）。
            # semaphore 反映「已绑定的 chat_session 数」，达 scope_concurrency 上限后新 session
            # 在此阻塞，等 TTL 老化(unbind)释放槽位。槽位占用记录在 scope_handler._acquired_slots，
            # 由 unbind/remove_endpoint 精确归还。
            _t0 = time.monotonic()
            _acquired = await scope_handler.acquire_slot(chat_session_id)
            _wait = time.monotonic() - _t0
            if _acquired and _wait > 0.5:
                logger.info(
                    "⚡ chat_session 排队等待 %.1fs 后获取槽位: scope_id=%s chat_session_id=%s",
                    _wait, scope_id, chat_session_id,
                )
            armed = False
            try:
                async with self._lock:
                    # 锁内二次确认：同一 chat_session 的并发请求可能已绑定（gateway 通常串行化，
                    # 此为防御），若发现已绑定则归还多余槽位，避免重复占用。
                    if _acquired and scope_handler.is_chat_session_bound(chat_session_id):
                        scope_handler.release_slot(chat_session_id)
                        _acquired = False
                    endpoint = scope_handler.pick_or_bind(chat_session_id)
                    if endpoint is None:
                        # 所有端点满 / 无端点 → 扩容（每次仅 +1 Pod；need = reserve_per_pod）
                        # max_scope_pods 安全闸：达上限不再扩容
                        if scope_handler.endpoint_count < scope_handler.max_scope_pods:
                            pod_handler = await self._sm.pick_or_create_pod(sreq)
                            if pod_handler is not None and pod_handler.try_reserve_session_quota(
                                scope_id, reserve_per_pod
                            ):
                                scope_handler.add_endpoint(pod_handler)
                                scope_handler.bind(chat_session_id, pod_handler.id)
                                endpoint = pod_handler
                            elif pod_handler is not None:
                                # 预留失败：记录孤儿，锁外推动 in_use→idle 回收
                                failed_to_reserve = pod_handler
                                endpoint = None
                            else:
                                endpoint = None

                if failed_to_reserve is not None:
                    # 预留失败的实例（含 pick_or_create_pod 新 deploy 的）：推动 in_use→idle 回收，
                    # service_ttl 到期后转入 idle，避免无 session/inflight 的孤儿 Pod 永久驻留。
                    try:
                        await self._sm.reconsider_idle_transition(failed_to_reserve.id)
                    except Exception as e:  # noqa: BLE001
                        logger.error("推动孤儿实例回收失败: %s", e, exc_info=True)
                    if _acquired:
                        scope_handler.release_slot(chat_session_id)  # 绑定失败，回退槽位
                        _acquired = False
                    await self._fail(w, 100001, session_id=scope_id)
                    return
                if endpoint is None:
                    if _acquired:
                        scope_handler.release_slot(chat_session_id)  # 达 max_scope_pods，回退槽位
                        _acquired = False
                    await self._fail(w, 100001, session_id=scope_id)
                    return

                armed = True  # chat_session 已绑定端点，即将发送

                # 转发到已绑定端点（scope_handler.handle_message 内部解析亲和端点）
                pod_name = self._pod_name_for(endpoint.endpoint_id)
                logger.info(
                    "路由到服务实例: scope_id=%s chat_session_id=%s request_id=%s pod=%s",
                    scope_id, chat_session_id, sreq.request_id, pod_name,
                )
                await scope_handler.handle_message(w)
                logger.debug(
                    "已完成消息处理: scope_id=%s chat_session_id=%s request_id=%s pod=%s",
                    scope_id, chat_session_id, sreq.request_id, pod_name,
                )
            finally:
                # 注意：绑定成功的 chat_session 不在此释放 semaphore ——
                # 槽位由 per-chat_session TTL 老化（unbind）时释放，使排队中的新 session 进入。
                # arm chat_session TTL（或 ttl<=0 走 pending 立即清理）——发送结束之后才计老化
                if armed and chat_session_id:
                    if sreq.session_ttl > 0:
                        try:
                            await self._arm_chat_session_timer(
                                chat_session_id, sreq.session_ttl, scope_id
                            )
                        except Exception as e2:  # noqa: BLE001
                            logger.error("arm chat_session 计时器失败: %s", e2, exc_info=True)
                    else:
                        eid = scope_handler.affined_endpoint(chat_session_id)
                        if eid is not None:
                            self._pending_chat_expiry[chat_session_id] = (scope_id, eid)
                            await self.flush_pending_for_chat_session(chat_session_id)
        except asyncio.CancelledError:
            # cancel落在 acquire 后/bind 前时归还槽位，防 semaphore 泄漏；
            # 已绑定的由 TTL 老化释放，不在此处理。
            if _acquired and scope_handler is not None and not scope_handler.is_chat_session_bound(chat_session_id):
                scope_handler.release_slot(chat_session_id)
            logger.error("scope_id=%s chat_session_id=%s 被中断执行", scope_id, chat_session_id)
            await self._fail(w, 100003, session_id=scope_id)
        except Exception as e:  # noqa: BLE001
            logger.error("路由/处理过程异常, scope_id=%s: %s", scope_id, e, exc_info=True)
            await self._fail(w, 100002, session_id=scope_id)

    def _pod_name_for(self, service_id: Optional[str]) -> str:
        if not service_id:
            return "unknown"
        h = self._sm.find_service_handler(service_id)
        if h is not None and getattr(h, "pod_info", None):
            return getattr(h.pod_info, "pod_name", "unknown")
        return "unknown"

    # ==================== TTL / 过期 ====================

    async def _arm_chat_session_timer(
        self, chat_session_id: str, ttl: int, scope_id: str
    ) -> None:
        if ttl <= 0:
            return
        key = f"csess:{chat_session_id}"
        await self._timer.cancel_timer(key)
        # 重新 arm 等同于「chat_session 又活跃」，清掉旧的延期清理标记
        self._pending_chat_expiry.pop(chat_session_id, None)

        async def _expired() -> None:
            await self._on_chat_session_expired(chat_session_id, scope_id)

        await self._timer.start_timer(key, ttl, _expired)
        logger.info(
            "已 arm chat_session TTL: chat_session_id=%s scope_id=%s ttl=%s",
            chat_session_id, scope_id, ttl,
        )

    async def _on_chat_session_expired(self, chat_session_id: str, scope_id: str) -> None:
        """chat_session TTL 到期：若仍有 inflight 则入 pending；否则老化（unbind + 条件释放端点）。"""
        logger.info("chat_session TTL 到期, 准备回收: chat_session_id=%s", chat_session_id)
        emptied_endpoint_id: Optional[str] = None
        async with self._lock:
            scope_handler = self._registry.get(scope_id)
            if scope_handler is None:
                return

            # 该 chat_session 仍有 inflight（响应未结束）→ 延期，等 inflight 归零重试
            if chat_session_id in scope_handler.active_chat_sessions:
                eid = scope_handler.affined_endpoint(chat_session_id)
                if eid is not None:
                    self._pending_chat_expiry[chat_session_id] = (scope_id, eid)
                logger.info(
                    "chat_session 到期但仍有 inflight, 入延期清理队列: chat_session_id=%s",
                    chat_session_id,
                )
                return

            emptied_endpoint_id = await self._expire_chat_session_locked(
                chat_session_id, scope_id
            )
        # 锁外：evict 释放 quota 后，主动让 Pod 重新评估 in_use→idle
        if emptied_endpoint_id is not None:
            try:
                await self._sm.reconsider_idle_transition(emptied_endpoint_id)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "reconsider_idle_transition 失败: endpoint_id=%s err=%s",
                    emptied_endpoint_id, e, exc_info=True,
                )

    async def _expire_chat_session_locked(
        self, chat_session_id: str, scope_id: str
    ) -> Optional[str]:
        """执行单个 chat_session 的老化（调用方须持 ``self._lock`` 且已确认无 inflight）。

        unbind → 若该端点本 scope 的最后一个 chat_session：``evict_session`` 释放预留块 +
        ``remove_endpoint`` 摘路由 → 若 scope_handler 空：``registry.remove``。
        返回被清空的 endpoint_id（供锁外 ``reconsider_idle_transition``），无则 None。
        """
        scope_handler = self._registry.get(scope_id)
        if scope_handler is None:
            self._pending_chat_expiry.pop(chat_session_id, None)
            return None
        endpoint_id = scope_handler.unbind(chat_session_id)
        emptied: Optional[str] = None
        if endpoint_id is not None and scope_handler.endpoint_session_count(endpoint_id) == 0:
            # 该端点本 scope 最后一个 chat_session 老化：释放预留块 + 摘路由
            pod_handler = self._sm.find_service_handler(endpoint_id)
            if pod_handler is not None:
                try:
                    await pod_handler.evict_session(scope_id)
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "evict_session 失败: scope_id=%s endpoint_id=%s err=%s",
                        scope_id, endpoint_id, e, exc_info=True,
                    )
            scope_handler.remove_endpoint(endpoint_id)
            emptied = endpoint_id
            logger.info(
                "端点计数归零, 已释放并摘除: scope_id=%s endpoint_id=%s",
                scope_id, endpoint_id,
            )
        # scope_handler 无 chat_session 且无端点 → 从 registry 移除
        if scope_handler.is_empty():
            await self._registry.remove(scope_id)
            logger.info("scope_handler 已空, 从 registry 移除: scope_id=%s", scope_id)
        self._pending_chat_expiry.pop(chat_session_id, None)
        return emptied

    async def flush_pending_for_service(self, service_id: str) -> None:
        """Pod inflight→0 回调：清理挂在该 Pod 上「到期但仍 inflight（现已归零）」的 chat_session。

        由 ServiceManager 在 ``_on_in_use_may_move_to_idle_pool`` / ``reconsider_idle_transition`` 回调。
        """
        emptied_list: List[str] = []
        async with self._lock:
            targets = [
                cs for cs, (_sid, eid) in self._pending_chat_expiry.items()
                if eid == service_id
            ]
            for cs in targets:
                scope_id = self._pending_chat_expiry.get(cs, (None, None))[0]
                if scope_id is None:
                    self._pending_chat_expiry.pop(cs, None)
                    continue
                scope_handler = self._registry.get(scope_id)
                if scope_handler is not None and cs in scope_handler.active_chat_sessions:
                    continue  # 仍 inflight，跳过
                emptied = await self._expire_chat_session_locked(cs, scope_id)
                if emptied is not None:
                    emptied_list.append(emptied)
        for emptied in emptied_list:
            try:
                await self._sm.reconsider_idle_transition(emptied)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "reconsider_idle_transition 失败: endpoint_id=%s err=%s",
                    emptied, e, exc_info=True,
                )

    async def flush_pending_for_chat_session(self, chat_session_id: str) -> None:
        """单个 chat_session 的 pending 立即清理（ttl<=0 时由 handle_user_request 调用）。"""
        emptied: Optional[str] = None
        async with self._lock:
            entry = self._pending_chat_expiry.get(chat_session_id)
            if entry is None:
                return
            scope_id, _eid = entry
            scope_handler = self._registry.get(scope_id)
            if scope_handler is not None and chat_session_id in scope_handler.active_chat_sessions:
                return  # 仍 inflight
            emptied = await self._expire_chat_session_locked(chat_session_id, scope_id)
        if emptied is not None:
            try:
                await self._sm.reconsider_idle_transition(emptied)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "reconsider_idle_transition 失败: endpoint_id=%s err=%s",
                    emptied, e, exc_info=True,
                )

    # ==================== 失败响应 ====================

    async def _fail(
        self, w: ScopeRequestWrapper, code: int, *, session_id: str | None = None
    ) -> None:
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
        sreq = w.session_request
        rid = sreq.request_id or ""
        channel_id = str(sreq.channel_id or "")
        await w.response_queue.put(
            {
                "request_id": rid,
                "channel_id": channel_id,
                "is_complete": True,
                "payload": {
                    "error": em.message,
                    "message": em.message,
                },
            }
        )
        if w.cancel and not w.cancel.done():
            w.cancel.set_result(None)

    # ==================== 生命周期（由 ServiceManager.stop / 监控调用）====================

    async def on_pod_removed(self, service_id: str) -> None:
        """某 Pod 已失效被移除：从所有引用它的 ServiceScopeHandler 中摘除该 endpoint。

        - 单 endpoint 的 scope：摘除后 endpoint_count==0，下条消息会触发 pick_or_create 重新装填；
        - 多 endpoint 的 scope：摘除该 endpoint，其余继续服务；
        - 若摘除后 scope_handler 变空（is_empty），就地 registry.remove，避免空 handler 滞留；
        - 同时清理 pending 中指向该 Pod 的记录。
        """
        async with self._lock:
            for sid in self._registry.all_session_ids():
                scope_handler = self._registry.get(sid)
                if scope_handler is None or not scope_handler.has_endpoint(service_id):
                    continue
                scope_handler.remove_endpoint(service_id)
                logger.info(
                    "已从 scope 摘除失效 endpoint: scope_id=%s service_id=%s remaining=%s",
                    sid, service_id, scope_handler.endpoint_count,
                )
                if scope_handler.is_empty():
                    await self._registry.remove(sid)
                    logger.info(
                        "scope_handler 摘除后为空, 从 registry 移除: scope_id=%s", sid
                    )
            # 清理指向该 Pod 的 pending 记录
            stale = [
                cs for cs, (_sid, eid) in self._pending_chat_expiry.items()
                if eid == service_id
            ]
            for cs in stale:
                self._pending_chat_expiry.pop(cs, None)

    async def shutdown(self) -> None:
        """清理所有 chat_session TTL 计时与 pending 标记（ServiceScopeHandler 由 registry 丢弃）。"""
        for sid in list(self._registry.all_session_ids()):
            scope_handler = self._registry.get(sid)
            if scope_handler is None:
                continue
            for csid in scope_handler.chat_session_ids:
                try:
                    await self._timer.cancel_timer(f"csess:{csid}")
                except Exception as e:  # noqa: BLE001
                    logger.debug("cancel csess timer: %s", e)
        self._pending_chat_expiry.clear()
