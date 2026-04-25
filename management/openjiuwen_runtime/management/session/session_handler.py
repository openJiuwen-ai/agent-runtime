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
    """同 session 内用 Semaphore 限制最大并行请求数（来自 ISessionRequest.session_concurrency）。"""

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

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def active_rids(self) -> Set[str]:
        return set(self._active_rids)

    async def handle_message(self, msg: SessionRequestWrapper) -> None:
        parent = self._parent_ref()
        if parent is None:
            return
        await self._sem.acquire()
        rid: Optional[str] = None
        if msg.session_request.request_id:
            rid = msg.session_request.request_id
            self._active_rids.add(rid)
            await self._session_router.set_request_session(rid, self._session_id)
        try:
            await parent.invoke_channel(
                msg,
            )
        finally:
            if rid:
                self._active_rids.discard(rid)
                await self._session_router.delete_request_session(rid)
            self._sem.release()
