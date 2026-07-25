# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Session 运行时管理：SessionHandler 归属、消息入口、session TTL/亲和/pending 过期。

第二层解耦：从 ServiceManager 抽取的 session 编排职责。与 ServiceManager（Pod 池）协作：
- 向 ServiceManager 借/还 endpoint（Pod）；
- ServiceManager 在 Pod inflight 归零时回调 :meth:`flush_pending_for_service`。

内含 :class:`SessionRegistry`（SessionHandler 归属，替代原 ServiceHandler._sessions）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .exception import exception_message
from .interfaces import (
    IServiceHandler,
    ITimer,
    RawMessage,
    SessionRequestWrapper,
)
from .router import SessionRouter
from .session_handler import SessionHandler

if TYPE_CHECKING:
    # 仅用于类型提示，避免与 ServiceManager 的运行时循环导入
    from .service_manager import ServiceManager

logger = get_logger(__name__)


class SessionRegistry:
    """SessionHandler 的归属与生命周期管理（替代原 ServiceHandler._sessions）。

    每个 session_id 对应一个 SessionHandler；endpoints 由 SessionRuntimeManager 装填。
    """

    def __init__(self, session_router: SessionRouter) -> None:
        self._lock = asyncio.Lock()
        self._handlers: Dict[str, SessionHandler] = {}
        self._session_router = session_router

    @property
    def session_router(self) -> SessionRouter:
        return self._session_router

    async def get_or_create(
        self, session_id: str, max_parallel: int,
    ) -> SessionHandler:
        """获取或创建 SessionHandler（endpoints 为空，由 SessionRuntimeManager 装填）。"""
        async with self._lock:
            sh = self._handlers.get(session_id)
            if sh is None:
                sh = SessionHandler(
                    session_id, max_parallel, [], self._session_router
                )
                self._handlers[session_id] = sh
                logger.info(
                    "SessionRegistry 新建 SessionHandler: session_id=%s max_parallel=%s",
                    session_id, max_parallel,
                )
            return sh

    def get(self, session_id: str) -> Optional[SessionHandler]:
        return self._handlers.get(session_id)

    def has_session(self, session_id: str) -> bool:
        return session_id in self._handlers

    async def remove(self, session_id: str) -> Optional[SessionHandler]:
        async with self._lock:
            return self._handlers.pop(session_id, None)

    def active_count(self) -> int:
        return len(self._handlers)

    def all_session_ids(self) -> List[str]:
        return list(self._handlers.keys())


class SessionRuntimeManager:
    """Session 生命周期与编排（从 ServiceManager 抽取）。

    职责：消息入口、session TTL、pending 过期清理、向 Pod 池借/还 endpoint。
    """

    def __init__(
        self,
        timer: ITimer,
        service_manager: "ServiceManager",
    ) -> None:
        self._timer = timer
        self._sm = service_manager
        self._registry = SessionRegistry(SessionRouter())
        self._lock = asyncio.Lock()
        # session_id -> 代表性 service_id（TTL 到期时仍有 inflight 的延期清理队列）
        self._pending_expired: Dict[str, str] = {}

    @property
    def registry(self) -> SessionRegistry:
        return self._registry

    # ==================== 消息入口 ====================

    async def handle_user_request(self, raw: RawMessage) -> None:
        w = raw.message
        if not isinstance(w, SessionRequestWrapper):
            logger.error("用户消息体类型错误, 预期 SessionRequestWrapper, 实际 %s", type(w))
            return
        sreq = w.session_request
        session_id = sreq.session_id

        if self._sm.is_deprecated():
            logger.warning(
                "待老化的 ServiceManager 正在处理新请求: session_id=%s request_id=%s",
                session_id,
                sreq.request_id,
            )

        # 新消息进入：先取消该 session 待生效的 session_ttl 计时与延期清理标记
        await self._timer.cancel_timer(f"sess:{session_id}")
        self._pending_expired.pop(session_id, None)

        sh: Optional[SessionHandler] = None
        failed_to_reserve: Optional[IServiceHandler] = None
        try:
            sh = await self._registry.get_or_create(session_id, sreq.session_concurrency)

            # 确保 endpoint（Pod）就绪：首次装填 1 个 endpoint（多 Pod 扩缩容见 multi-pod）
            async with self._lock:
                if not sh.has_routable_endpoint():
                    h = await self._sm.pick_or_create_pod(sreq)
                    if h is None:
                        await self._fail(w, 100001, session_id=session_id)
                        return
                    if not h.try_reserve_session_quota(session_id, sreq.session_concurrency):
                        logger.warning(
                            "服务额度预留失败(资源不足): session_id=%s service_id=%s "
                            "need=%s avail=%s",
                            session_id, h.id, sreq.session_concurrency, h.available_concurrency,
                        )
                        # 记录失败的实例, 锁外推动其走 in_use→idle 回收; 仅置空 h 会导致
                        # pick_or_create_pod 刚放入 _in_use 的实例成为孤儿 (无 session/inflight
                        # 永不触发 idle 回收钩子), Pod 永久驻留
                        failed_to_reserve = h
                    else:
                        sh.add_endpoint(h)
                        logger.info(
                            "session 已绑定到实例: session_id=%s -> service_id=%s",
                            session_id, h.id,
                        )

            # 预留失败的实例 (含 pick_or_create_pod 新 deploy 的): 推动 in_use→idle 回收
            # service_ttl 到期后转入 idle, 若 idle>min_idle 则被 excess_idle 回收删 Pod;
            # 若 idle<=min_idle 则作为热备保留。_arm_in_use_to_idle_pool 内部有
            # inflight/session 占用保护, 不会错误 arm。
            if failed_to_reserve is not None:
                try:
                    await self._sm.reconsider_idle_transition(failed_to_reserve.id)
                except Exception as e:  # noqa: BLE001
                    logger.error("推动孤儿实例回收失败: %s", e, exc_info=True)
                await self._fail(w, 100001, session_id=session_id)
                return

            # 转发到 SessionHandler（不再经过 ServiceHandler.handle_message）
            endpoint_id = self._peek_endpoint_id(sh)
            pod_name = self._pod_name_for(endpoint_id)
            logger.info(
                "路由到服务实例: service_id=%s session_id=%s request_id=%s pod=%s",
                endpoint_id, session_id, sreq.request_id, pod_name,
            )
            await sh.handle_message(w)
            logger.debug(
                "已完成消息处理: session_id=%s request_id=%s pod=%s",
                session_id, sreq.request_id, pod_name,
            )
        except asyncio.CancelledError:
            logger.error("session_id=%s 被中断执行", session_id)
            await self._fail(w, 100003, session_id=session_id)
        except Exception as e:  # noqa: BLE001
            logger.error("路由/处理过程异常, session_id=%s: %s", session_id, e, exc_info=True)
            await self._fail(w, 100002, session_id=session_id)
        finally:
            # 请求结束: 按 session_ttl 维持映射；ttl<=0 视为「不保留」立即标记 pending 等待 flush
            if sh is not None:
                if sreq.session_ttl > 0:
                    try:
                        await self._arm_session_timer(session_id, sreq.session_ttl)
                    except Exception as e2:  # noqa: BLE001
                        logger.error("arm session 计时器失败: %s", e2, exc_info=True)
                    else:
                        logger.debug(
                            "session active: session_id=%s ttl=%s",
                            session_id, sreq.session_ttl,
                        )
                else:
                    rep = self._peek_endpoint_id(sh)
                    if rep is not None:
                        self._pending_expired[session_id] = rep
                        logger.debug(
                            "session ttl<=0 pending expired: session_id=%s service_id=%s",
                            session_id, rep,
                        )
                        # 由 inflight 归零钩子触发 flush；此处主动尝试一次
                        await self.flush_pending_for_service(rep)

    def _peek_endpoint_id(self, sh: SessionHandler) -> Optional[str]:
        ids = sh.endpoint_ids
        return ids[0] if ids else None

    def _pod_name_for(self, service_id: Optional[str]) -> str:
        if not service_id:
            return "unknown"
        h = self._sm.find_service_handler(service_id)
        if h is not None and getattr(h, "pod_info", None):
            return getattr(h.pod_info, "pod_name", "unknown")
        return "unknown"

    # ==================== TTL / 过期 ====================

    async def _arm_session_timer(self, session_id: str, ttl: int) -> None:
        if ttl <= 0:
            return
        key = f"sess:{session_id}"
        await self._timer.cancel_timer(key)
        # 重新 arm 等同于「session 又活跃」, 清掉旧的延期清理标记
        self._pending_expired.pop(session_id, None)

        async def _expired() -> None:
            await self._on_session_expired(session_id)

        await self._timer.start_timer(key, ttl, _expired)
        logger.info("已 arm session TTL 计时: session_id=%s ttl=%s", session_id, ttl)

    async def _on_session_expired(self, session_id: str) -> None:
        """session_ttl 到期: 若该 session 已无 inflight 则立即移除并触发 flush；否则入 pending 等待。"""
        logger.info("session TTL 到期, 准备回收: session_id=%s", session_id)
        target_svc: Optional[str] = None
        removed = False
        async with self._lock:
            sh = self._registry.get(session_id)
            if sh is None:
                return

            # 跨所有 endpoint 的在途请求数
            if len(sh.active_rids) > 0:
                rep = self._peek_endpoint_id(sh)
                if rep is not None:
                    self._pending_expired[session_id] = rep
                logger.info(
                    "session 到期但仍有 inflight, 入延期清理队列: session_id=%s rids=%s",
                    session_id, len(sh.active_rids),
                )
                return

            # 在途已空：逐 Pod 释放 quota + 取消，再销毁 SessionHandler
            evicted_endpoints: list[str] = []
            for endpoint_id in sh.endpoint_ids:
                h = self._sm.find_service_handler(endpoint_id)
                if h is not None:
                    await h.evict_session(session_id)
                    evicted_endpoints.append(endpoint_id)
                if target_svc is None:
                    target_svc = endpoint_id
            await self._registry.remove(session_id)
            self._pending_expired.pop(session_id, None)
            removed = True
            logger.info(
                "session 已移除并归还 service 并发: session_id=%s", session_id,
            )
        if removed:
            # evict 释放 quota 后，inflight 多已归零、归零 hook 不会再触发；
            # 主动让 Pod 层重新评估 in_use→idle 转换，否则工作 Pod 会卡在 in_use 池不老化。
            for endpoint_id in evicted_endpoints:
                try:
                    await self._sm.reconsider_idle_transition(endpoint_id)
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "reconsider_idle_transition 失败: service_id=%s err=%s",
                        endpoint_id, e, exc_info=True,
                    )
            if target_svc:
                await self.flush_pending_for_service(target_svc)

    async def flush_pending_for_service(self, service_id: str) -> None:
        """清理挂在某 Pod 上的「到期但仍 inflight」的 session；清理后该 Pod 可能可转入 idle。

        由 ServiceManager 在 Pod inflight 归零时回调（``_on_in_use_may_move_to_idle_pool``）。
        """
        async with self._lock:
            sids = [
                sid
                for sid, svc in list(self._pending_expired.items())
                if svc == service_id
            ]
            for sid in sids:
                sh = self._registry.get(sid)
                if sh is None:
                    self._pending_expired.pop(sid, None)
                    continue
                if len(sh.active_rids) == 0:
                    for endpoint_id in sh.endpoint_ids:
                        h = self._sm.find_service_handler(endpoint_id)
                        if h is not None:
                            await h.evict_session(sid)
                    await self._registry.remove(sid)
                    self._pending_expired.pop(sid, None)
                    logger.info(
                        "延期清理已移除 session: session_id=%s service_id=%s",
                        sid, service_id,
                    )

    # ==================== 失败响应 ====================

    async def _fail(
        self, w: SessionRequestWrapper, code: int, *, session_id: str | None = None
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
        # (request_id + channel_id + is_complete + payload, 且无 ok), 否则上游抛
        # ValueError: unrecognized wire shape, 导致前端收不到错误信息。
        # request_id 是 WSS 多路复用硬条件, 缺失会被 dispatch_inbound_chunk 丢弃。
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

    # ==================== 生命周期（由 ServiceManager.stop 调用）====================

    async def on_pod_removed(self, service_id: str) -> None:
        """某 Pod 已失效被移除：从所有引用它的 SessionHandler 中摘除该 endpoint。

        - 单 endpoint 的 session：摘除后 endpoint_count==0，下条消息会触发 pick_or_create 重新装填；
        - 多 endpoint 的 session：摘除该 endpoint，其余继续服务；
        - 同时清理 pending_expired 中指向该 Pod 的记录。
        """
        async with self._lock:
            for sid in self._registry.all_session_ids():
                sh = self._registry.get(sid)
                if sh is None or not sh.has_endpoint(service_id):
                    continue
                sh.remove_endpoint(service_id)
                logger.info(
                    "已从 session 摘除失效 endpoint: session_id=%s service_id=%s remaining=%s",
                    sid, service_id, sh.endpoint_count,
                )
            # 清理指向该 Pod 的 pending 记录
            stale = [sid for sid, svc in self._pending_expired.items() if svc == service_id]
            for sid in stale:
                self._pending_expired.pop(sid, None)

    async def shutdown(self) -> None:
        """清理所有 session TTL 计时与 pending 标记（SessionHandler 由 registry 丢弃）。"""
        for sid in list(self._registry.all_session_ids()):
            await self._timer.cancel_timer(f"sess:{sid}")
        self._pending_expired.clear()
