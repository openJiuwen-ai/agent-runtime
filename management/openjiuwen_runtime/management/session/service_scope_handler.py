# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""单 scope 限流 + 多发送端点路由（chat_session 亲和，与 ServiceHandler 类型解耦）。

ServiceScopeHandler 不再引用 ServiceHandler 类型，仅依赖 :class:`ISendEndpoint` 接口。
单 Pod 持 1 端点；多 Pod 持 N 端点，按「chat_session 亲和 → 首个未满端点」路由，全满则
返回 None 由编排层扩容。

并发位（semaphore，上限 = scope_concurrency）由编排层在主流程首尾 :meth:`acquire` /
:meth:`release`（= 活跃 chat_session 闸门）；本类只负责路由 / 亲和 / 发送。
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Set

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import IResponseParser, ISendEndpoint, IServiceScopeHandler, ScopeRequestWrapper
from .router import SessionRouter

logger = get_logger(__name__)


class ServiceScopeHandler(IServiceScopeHandler):
    """同 scope 内限流 + 多端点路由（chat_session 亲和）。

    semaphore(scope_concurrency) 由编排层 acquire/release；路由策略：chat_session 亲和 →
    首个 ``inflight<reserve_per_pod`` 的可路由端点；都满返回 None（编排层据此扩容）。
    """

    def __init__(
        self,
        service_id: str,
        max_parallel: int,
        endpoints: List[ISendEndpoint],
        session_router: SessionRouter,
        reserve_per_pod: int,
    ) -> None:
        self._service_id = service_id
        m = max(1, int(max_parallel))
        self._sem = asyncio.BoundedSemaphore(m)
        self._endpoints: List[ISendEndpoint] = list(endpoints)
        self._session_router = session_router
        self._reserve_per_pod = max(1, int(reserve_per_pod))
        # scope_concurrency 与 max_scope_pods（per-scope Pod 上限，安全闸）
        self._scope_concurrency = m
        self._max_scope_pods = max(
            1, (m + self._reserve_per_pod - 1) // self._reserve_per_pod
        )
        # chat_session 亲和: chat_session_id -> endpoint_id
        self._chat_affinity: Dict[str, str] = {}
        # endpoint_id -> 已绑 chat_session_id 集
        self._endpoint_sessions: Dict[str, Set[str]] = {
            ep.endpoint_id: set() for ep in self._endpoints
        }
        # 当前有 inflight 的 chat_session 引用计数（同一 chat_session 多并发 inflight 时计数；
        # TTL 延期判定用：refcount>0 即视为仍活跃）。注：正常前提下一 chat_session 仅 1 inflight。
        self._active_chat_session_refs: Dict[str, int] = {}
        self._active_rids: Set[str] = set()
        # 已占用 semaphore 槽位的 chat_session 集（acquire_slot 时加入，release_slot 时移除）。
        # 解耦「槽位占用」与「端点绑定」：bind/unbind 可在 acquire 之前/之后，release 仅归还真正占用过的槽位，
        # 避免 unbind 无条件 release 导致 BoundedSemaphore 越界（测试直接 bind/unbind 场景）。
        self._acquired_slots: Set[str] = set()
        logger.debug(
            "ServiceScopeHandler 构造: service_id=%s max_parallel=%s "
            "reserve_per_pod=%s endpoints=%s",
            self._service_id, m, self._reserve_per_pod,
            [ep.endpoint_id for ep in self._endpoints],
        )

    # ==================== 身份与状态 ====================

    @property
    def service_id(self) -> str:
        return self._service_id

    @property
    def active_rids(self) -> Set[str]:
        return set(self._active_rids)

    @property
    def active_chat_sessions(self) -> Set[str]:
        """当前仍有 inflight 的 chat_session 集（refcount>0），供 TTL 到期判定是否延期。"""
        return {cs for cs, n in self._active_chat_session_refs.items() if n > 0}

    @property
    def chat_session_ids(self) -> List[str]:
        """当前已绑 chat_session id 列表（供 shutdown 取消计时器）。"""
        return list(self._chat_affinity.keys())

    @property
    def endpoint_count(self) -> int:
        """当前持有的发送端点数（多 Pod 扩缩容的依据）。"""
        return len(self._endpoints)

    @property
    def endpoint_ids(self) -> List[str]:
        """当前端点 id 列表（供编排层遍历 evict）。"""
        return [ep.endpoint_id for ep in self._endpoints]

    @property
    def reserve_per_pod(self) -> int:
        return self._reserve_per_pod

    @property
    def max_scope_pods(self) -> int:
        """单 scope 可弹性到的 Pod 数上限 = ceil(scope_concurrency / reserve_per_pod)。"""
        return self._max_scope_pods

    def is_chat_session_bound(self, chat_session_id: Optional[str]) -> bool:
        """该 chat_session 是否已绑定到某端点（已绑定则已占用 semaphore 槽位）。"""
        return bool(chat_session_id) and chat_session_id in self._chat_affinity

    # ==================== semaphore（由编排层驱动；绑定级）====================

    async def acquire_slot(self, chat_session_id: Optional[str]) -> bool:
        """新 chat_session 占用并发槽位（满则在此阻塞排队）。

        仅未绑定且有亲和标识的 chat_session 才 acquire；已绑定/无亲和返回 False（不占槽位）。
        占用记录到 ``_acquired_slots``，供 release_slot/unbind/remove_endpoint 精确归还。
        """
        if not chat_session_id or chat_session_id in self._chat_affinity:
            return False
        await self._sem.acquire()
        self._acquired_slots.add(chat_session_id)
        return True

    def release_slot(self, chat_session_id: Optional[str]) -> None:
        """归还并发槽位（仅当 acquire_slot 真正占用过时才 release，幂等）。"""
        if chat_session_id and chat_session_id in self._acquired_slots:
            self._acquired_slots.discard(chat_session_id)
            self._sem.release()

    # ==================== 端点管理 ====================

    def add_endpoint(self, endpoint: ISendEndpoint) -> None:
        """弹性扩容：追加一个发送端点。"""
        if any(ep.endpoint_id == endpoint.endpoint_id for ep in self._endpoints):
            logger.debug(
                "ServiceScopeHandler 忽略重复 endpoint: service_id=%s endpoint_id=%s",
                self._service_id, endpoint.endpoint_id,
            )
            return
        self._endpoints.append(endpoint)
        self._endpoint_sessions.setdefault(endpoint.endpoint_id, set())
        logger.info(
            "ServiceScopeHandler 添加 endpoint: service_id=%s endpoint_id=%s endpoint_count=%s",
            self._service_id, endpoint.endpoint_id, len(self._endpoints),
        )

    def remove_endpoint(self, endpoint_id: str) -> bool:
        """弹性缩容：移除一个发送端点。

        Returns:
            True 表示已移除；False 表示未找到。
        """
        for i, ep in enumerate(self._endpoints):
            if ep.endpoint_id == endpoint_id:
                self._endpoints.pop(i)
                self._endpoint_sessions.pop(endpoint_id, None)
                # 清除指向该 endpoint 的亲和，并释放对应 chat_session 的并发槽位
                # （否则 Pod 不健康被摘除时 semaphore 会泄漏）
                removed = [
                    cs for cs, eid in self._chat_affinity.items() if eid == endpoint_id
                ]
                self._chat_affinity = {
                    cs: eid for cs, eid in self._chat_affinity.items()
                    if eid != endpoint_id
                }
                for cs in removed:
                    self.release_slot(cs)
                logger.info(
                    "ServiceScopeHandler 移除 endpoint: service_id=%s endpoint_id=%s remaining=%s",
                    self._service_id, endpoint_id, len(self._endpoints),
                )
                return True
        return False

    def has_endpoint(self, endpoint_id: str) -> bool:
        return any(ep.endpoint_id == endpoint_id for ep in self._endpoints)

    def has_routable_endpoint(self) -> bool:
        """是否存在可路由的端点; 全部不可路由时编排层据此触发 pick_or_create_pod。"""
        return any(getattr(ep, "is_routable", True) for ep in self._endpoints)

    # ==================== chat_session 亲和 / 路由 ====================

    def pick_or_bind(self, chat_session_id: Optional[str]) -> Optional[ISendEndpoint]:
        """路由策略：亲和端点 → 首个未满端点（跳过不可路由的端点）。

        1. 亲和：同一 chat_session 始终路由到已绑定端点（可路由）。
        2. 首个未满：按 ``_endpoints`` 顺序，返回第一个**绑定 chat_session 数**
           ``<reserve_per_pod`` 的可路由端点，并记录亲和（chat_session_id 为空时不绑定）。
           注意：判断依据是已绑定的 chat_session 计数（持久，TTL 老化才释放），
           而非瞬时 inflight（请求完成即归零），否则绑定后槽位会被误判为空闲导致永不扩容。
        3. 都满 / 无端点 → ``None``（触发编排层扩容）。
        """
        # 1. 亲和
        if chat_session_id:
            affined_id = self._chat_affinity.get(chat_session_id)
            if affined_id:
                for ep in self._endpoints:
                    if ep.endpoint_id == affined_id and getattr(ep, "is_routable", True):
                        return ep
        # 2. 首个未满（按已绑定 chat_session 计数判断容量）
        for ep in self._endpoints:
            if not getattr(ep, "is_routable", True):
                continue
            if self.endpoint_session_count(ep.endpoint_id) < self._reserve_per_pod:
                if chat_session_id:
                    self.bind(chat_session_id, ep.endpoint_id)
                return ep
        return None

    def bind(self, chat_session_id: str, endpoint_id: str) -> None:
        """记录 chat_session → endpoint_id 亲和（幂等；跨端点改绑时清理旧端点集合）。"""
        if not chat_session_id:
            return
        prev = self._chat_affinity.get(chat_session_id)
        if prev == endpoint_id:
            return
        self._chat_affinity[chat_session_id] = endpoint_id
        self._endpoint_sessions.setdefault(endpoint_id, set()).add(chat_session_id)
        if prev is not None:
            old = self._endpoint_sessions.get(prev)
            if old is not None:
                old.discard(chat_session_id)
        logger.debug(
            "bind chat_session: service_id=%s chat_session_id=%s endpoint_id=%s",
            self._service_id, chat_session_id, endpoint_id,
        )

    def unbind(self, chat_session_id: str) -> Optional[str]:
        """解除 chat_session 亲和并释放并发槽位；返回其原先所在 endpoint_id。

        semaphore 槽位在 chat_session 首次绑定时 acquire（见编排层），此处对应 release，
        使排队中的新 chat_session 得以进入。
        """
        endpoint_id = self._chat_affinity.pop(chat_session_id, None)
        if endpoint_id is not None:
            sessions = self._endpoint_sessions.get(endpoint_id)
            if sessions is not None:
                sessions.discard(chat_session_id)
            self.release_slot(chat_session_id)  # chat_session 解绑，归还并发槽位（仅当占用过，幂等）
            logger.debug(
                "unbind chat_session: service_id=%s chat_session_id=%s endpoint_id=%s",
                self._service_id, chat_session_id, endpoint_id,
            )
        return endpoint_id

    def endpoint_session_count(self, endpoint_id: str) -> int:
        """该端点当前已绑 chat_session 数。"""
        return len(self._endpoint_sessions.get(endpoint_id, ()))

    def affined_endpoint(self, chat_session_id: str) -> Optional[str]:
        """chat_session 当前绑定的 endpoint_id（无则 None），供 TTL 延期记录归属端点。"""
        return self._chat_affinity.get(chat_session_id)

    def is_empty(self) -> bool:
        """无绑定 chat_session 且无端点（可从 registry 移除）。"""
        return not self._chat_affinity and not self._endpoints

    # ==================== 发送 ====================

    async def handle_message(self, msg: ScopeRequestWrapper) -> None:
        """向 chat_session 已绑定的端点发送请求（含 active 追踪）。

        端点选择与并发位由编排层在调用前后完成；本方法只解析已绑定端点并发送。
        """
        sreq = msg.session_request
        # 优先从 ISessionRequest.session_id 获取（实现层正确传递），回退到 raw_msg 解析
        chat_session_id: Optional[str] = None
        if hasattr(sreq, "session_id") and sreq.session_id:
            chat_session_id = sreq.session_id
        else:
            raw = sreq.raw_msg
            if raw is not None:
                # 与编排层 handle_user_request 一致：session_id 优先，缺失则回退 user_id
                chat_session_id = getattr(raw, "session_id", None) or getattr(raw, "user_id", None)

        endpoint: Optional[ISendEndpoint] = None
        if chat_session_id:
            affined_id = self._chat_affinity.get(chat_session_id)
            if affined_id:
                for ep in self._endpoints:
                    if ep.endpoint_id == affined_id:
                        endpoint = ep
                        break
        if endpoint is None:
            logger.error(
                "ServiceScopeHandler 无已绑定 endpoint, 丢弃消息: service_id=%s chat_session_id=%s",
                self._service_id, chat_session_id,
            )
            raise RuntimeError(
                f"ServiceScopeHandler {self._service_id} 无已绑定 endpoint "
                f"(chat_session_id={chat_session_id})"
            )

        rid: Optional[str] = sreq.request_id
        if rid:
            self._active_rids.add(rid)
            await self._session_router.set_request_session(rid, self._service_id)
        if chat_session_id:
            self._active_chat_session_refs[chat_session_id] = (
                self._active_chat_session_refs.get(chat_session_id, 0) + 1
            )

        logger.debug(
            "ServiceScopeHandler 发送: service_id=%s chat_session_id=%s request_id=%s endpoint=%s",
            self._service_id, chat_session_id, rid, endpoint.endpoint_id,
        )
        try:
            await endpoint.send_message(msg)
        finally:
            if rid:
                self._active_rids.discard(rid)
                await self._session_router.delete_request_session(rid)
            if chat_session_id:
                n = self._active_chat_session_refs.get(chat_session_id, 0)
                if n <= 1:
                    self._active_chat_session_refs.pop(chat_session_id, None)
                else:
                    self._active_chat_session_refs[chat_session_id] = n - 1
            logger.debug(
                "ServiceScopeHandler 发送完成: service_id=%s chat_session_id=%s request_id=%s endpoint=%s",
                self._service_id, chat_session_id, rid, endpoint.endpoint_id,
            )
