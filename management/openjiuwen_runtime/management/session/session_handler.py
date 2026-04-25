# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

import asyncio
import weakref
from typing import Optional, Set, TYPE_CHECKING

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import ISessionHandler, SessionRequestWrapper
from .router import SessionRouter

if TYPE_CHECKING:
    from .service_handler import ServiceHandler

logger = get_logger(__name__)


class SessionHandler(ISessionHandler):
    """同 session 内限流：最多 session_concurrency 路并行，其余在 BoundedSemaphore 上公平排队，不占用其他 session。"""

    def __init__(
        self,
        session_id: str,
        max_parallel: int,
        parent: "ServiceHandler",
        session_router: SessionRouter,
    ) -> None:
        self._session_id = session_id
        m = max(1, int(max_parallel))
        self._sem = asyncio.BoundedSemaphore(m)
        self._parent_ref: weakref.ref["ServiceHandler"] = weakref.ref(parent)
        self._session_router = session_router
        self._active_rids: Set[str] = set()
        logger.debug("SessionHandler 构造: session_id=%s max_parallel=%s", session_id, m)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def active_rids(self) -> Set[str]:
        return set(self._active_rids)

    async def handle_message(self, msg: SessionRequestWrapper) -> None:
        parent = self._parent_ref()
        if parent is None:
            # 服务实例已回收, 弱引用失效
            logger.error("SessionHandler parent 已失效, 丢弃消息 session_id=%s", self._session_id)
            return
        # 会话内并发位：满则在此等待, 不阻塞其它 session
        await self._sem.acquire()
        rid: Optional[str] = None
        if msg.session_request.request_id:
            rid = msg.session_request.request_id
            self._active_rids.add(rid)
            await self._session_router.set_request_session(rid, self._session_id)
        logger.debug(
            "SessionHandler 已获会话并发: session_id=%s request_id=%s 活跃rid数=%s",
            self._session_id,
            rid,
            len(self._active_rids),
        )
        try:
            await parent.invoke_channel(msg)
        finally:
            if rid:
                self._active_rids.discard(rid)
                await self._session_router.delete_request_session(rid)
            self._sem.release()
            logger.debug(
                "SessionHandler 释放会话并发: session_id=%s request_id=%s", self._session_id, rid
            )
