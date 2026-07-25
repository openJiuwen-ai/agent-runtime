# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""单 session 限流 + 多发送端点路由（与 ServiceHandler 类型解耦）。

ServiceScopeHandler 不再引用 ServiceHandler 类型，仅依赖 :class:`ISendEndpoint` 接口。
单 Pod 场景持有 1 个端点；多 Pod 场景持有 N 个端点，按「用户亲和 → 最少负载」路由。
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Set

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import IResponseParser, ISendEndpoint, IServiceScopeHandler, ScopeRequestWrapper
from .router import SessionRouter

logger = get_logger(__name__)


class ServiceScopeHandler(IServiceScopeHandler):
    """同 session 内限流 + 多端点路由。

    最多 ``session_concurrency`` 路并行，路由策略：用户亲和 → 最少负载。
    其余请求在 ``BoundedSemaphore`` 上公平排队，不占用其它 session 的并发位。
    """

    def __init__(
        self,
        service_id: str,
        max_parallel: int,
        endpoints: List[ISendEndpoint],
        session_router: SessionRouter,
    ) -> None:
        self._service_id = service_id
        m = max(1, int(max_parallel))
        self._sem = asyncio.BoundedSemaphore(m)
        self._endpoints: List[ISendEndpoint] = list(endpoints)
        self._session_router = session_router
        self._active_rids: Set[str] = set()
        # 用户亲和: user_id -> endpoint_id
        self._user_affinity: Dict[str, str] = {}
        logger.debug(
            "ServiceScopeHandler 构造: session_id=%s max_parallel=%s endpoints=%s",
            self._service_id, m, [ep.endpoint_id for ep in self._endpoints],
        )

    @property
    def service_id(self) -> str:
        return self._service_id

    @property
    def active_rids(self) -> Set[str]:
        return set(self._active_rids)

    @property
    def endpoint_count(self) -> int:
        """当前持有的发送端点数（多 Pod 扩缩容的依据）。"""
        return len(self._endpoints)

    @property
    def endpoint_ids(self) -> List[str]:
        """当前端点 id 列表（供编排层遍历 evict）。"""
        return [ep.endpoint_id for ep in self._endpoints]

    def add_endpoint(self, endpoint: ISendEndpoint) -> None:
        """弹性扩容：追加一个发送端点。"""
        if any(ep.endpoint_id == endpoint.endpoint_id for ep in self._endpoints):
            logger.debug(
                "ServiceScopeHandler 忽略重复 endpoint: session_id=%s endpoint_id=%s",
                self._service_id, endpoint.endpoint_id,
            )
            return
        self._endpoints.append(endpoint)
        logger.info(
            "ServiceScopeHandler 添加 endpoint: session_id=%s endpoint_id=%s endpoint_count=%s",
            self._service_id, endpoint.endpoint_id, len(self._endpoints),
        )

    def remove_endpoint(self, endpoint_id: str) -> bool:
        """弹性缩容：移除一个发送端点（不删最后一个）。

        Returns:
            True 表示已移除；False 表示未找到或为最后一个（拒绝移除）。
        """
        if len(self._endpoints) <= 1:
            logger.debug(
                "ServiceScopeHandler 拒绝移除最后一个 endpoint: session_id=%s endpoint_id=%s",
                self._service_id, endpoint_id,
            )
            return False
        for i, ep in enumerate(self._endpoints):
            if ep.endpoint_id == endpoint_id:
                self._endpoints.pop(i)
                # 清除指向该 endpoint 的用户亲和
                self._user_affinity = {
                    uid: eid for uid, eid in self._user_affinity.items()
                    if eid != endpoint_id
                }
                logger.info(
                    "ServiceScopeHandler 移除 endpoint: session_id=%s endpoint_id=%s remaining=%s",
                    self._service_id, endpoint_id, len(self._endpoints),
                )
                return True
        return False

    def has_endpoint(self, endpoint_id: str) -> bool:
        return any(ep.endpoint_id == endpoint_id for ep in self._endpoints)

    def _pick_endpoint(self, user_id: Optional[str]) -> Optional[ISendEndpoint]:
        """路由策略：用户亲和 → 最少负载。"""
        if not self._endpoints:
            return None

        # 1. 用户亲和：同一 user_id 始终路由到同一端点
        if user_id:
            affined_id = self._user_affinity.get(user_id)
            if affined_id:
                for ep in self._endpoints:
                    if ep.endpoint_id == affined_id:
                        return ep

        # 2. 最少负载：选择 inflight 最少的端点
        best = min(self._endpoints, key=lambda ep: ep.inflight)

        # 3. 记录亲和（首次分配）
        if user_id:
            self._user_affinity[user_id] = best.endpoint_id

        return best

    async def handle_message(self, msg: ScopeRequestWrapper) -> None:
        sreq = msg.session_request
        # 会话内并发位：满则在此等待，不阻塞其它 session
        await self._sem.acquire()
        rid: Optional[str] = None
        if sreq.request_id:
            rid = sreq.request_id
            self._active_rids.add(rid)
            await self._session_router.set_request_session(rid, self._service_id)

        user_id = None
        raw = sreq.raw_msg
        if raw is not None:
            user_id = getattr(raw, "user_id", None)

        endpoint = self._pick_endpoint(user_id)
        if endpoint is None:
            # 无可用端点：释放信号量并报错（编排层应保证至少 1 个端点）
            if rid:
                self._active_rids.discard(rid)
                await self._session_router.delete_request_session(rid)
            self._sem.release()
            logger.error(
                "ServiceScopeHandler 无可用 endpoint, 丢弃消息: session_id=%s request_id=%s",
                self._service_id, rid,
            )
            raise RuntimeError(f"ServiceScopeHandler {self._service_id} 无可用 endpoint")

        logger.debug(
            "ServiceScopeHandler 已获会话并发: session_id=%s request_id=%s "
            "endpoint=%s 活跃rid数=%s",
            self._service_id, rid, endpoint.endpoint_id, len(self._active_rids),
        )
        try:
            await endpoint.send_message(msg)
        finally:
            if rid:
                self._active_rids.discard(rid)
                await self._session_router.delete_request_session(rid)
            self._sem.release()
            logger.debug(
                "ServiceScopeHandler 释放会话并发: session_id=%s request_id=%s endpoint=%s",
                self._service_id, rid, endpoint.endpoint_id,
            )
