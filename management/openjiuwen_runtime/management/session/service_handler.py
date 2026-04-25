# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""单实例：服务级并发（单请求=1）、session 子处理器、部署与下行通道。"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import (
    IResponseParser,
    IServiceHandler,
    IServiceMessageChannel,
    SessionRequestWrapper,
)
from .router import SessionRouter
from .runtime import IDeployController, NoOpDeployController
from .session_handler import SessionHandler

logger = get_logger(__name__)

__all__ = ("ServiceHandler",)


class ServiceHandler(IServiceHandler):
    """单请求占 1 个服务并发；request_id 注册在 _by_request 供下行多路复用写回。"""

    def __init__(
        self,
        service_id: Optional[str] = None,
        *,
        total_concurrency: int = 200,
        message_channel: IServiceMessageChannel,
        response_parser: IResponseParser,
        deploy_controller: Optional[IDeployController] = None,
    ) -> None:
        if total_concurrency <= 0:
            raise ValueError("total_concurrency must be positive")
        self._id = service_id or str(uuid.uuid4())
        self._total = total_concurrency
        self._inflight = 0
        self._channel = message_channel
        self._parser = response_parser
        self._deploy: IDeployController = deploy_controller or NoOpDeployController()
        self._session_router = SessionRouter()
        self._sessions: Dict[str, SessionHandler] = {}
        self._by_request: Dict[str, SessionRequestWrapper] = {}
        self._pod_info: Any = None
        self._closed = False

    @property
    def id(self) -> str:
        return self._id

    @property
    def total_concurrency(self) -> int:
        return self._total

    @property
    def available_concurrency(self) -> int:
        return self._total - self._inflight

    @property
    def inflight_requests(self) -> int:
        return self._inflight

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)

    @property
    def pod_info(self) -> Any:
        return self._pod_info

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    async def handle_message(self, msg: SessionRequestWrapper) -> None:
        if self._closed:
            raise RuntimeError("ServiceHandler is closed")
        sreq = msg.session_request
        session_id = sreq.session_id
        max_sess = sreq.session_concurrency
        if max_sess <= 0:
            raise ValueError("session_concurrency must be positive")

        sh = self._sessions.get(session_id)
        if sh is None:
            sh = SessionHandler(
                session_id, max_sess, self, self._session_router
            )
            self._sessions[session_id] = sh
        await sh.handle_message(msg)

    async def invoke_channel(self, wrapper: SessionRequestWrapper) -> None:
        """由 SessionHandler 在取得 session 信号量后调用：占 1 个服务并发并下行。"""
        if self._inflight >= self._total:
            raise RuntimeError("insufficient service capacity")
        self._inflight += 1
        rid = wrapper.session_request.request_id
        if rid:
            self._by_request[rid] = wrapper

        async def _complete(r: Optional[str]) -> None:
            self._inflight = max(0, self._inflight - 1)
            if r:
                self._by_request.pop(r, None)

        try:
            await self._channel.send(
                self._id,
                wrapper,
                response_parser=self._parser,
                on_request_complete=_complete,
            )
        except Exception:
            await _complete(rid)
            raise

    async def dispatch_inbound_chunk(
        self, data: dict[str, Any], response_parser: IResponseParser
    ) -> bool:
        """长连接多路复用：按响应中的 request_id 写入对应 response_queue。"""
        rid = response_parser.request_id(data)
        if not rid:
            return False
        w = self._by_request.get(rid)
        if w is None:
            return False
        if w.cancel.done():
            return True
        await w.response_queue.put(data)
        return True

    async def remove_session(self, session_id: str) -> int:
        sh = self._sessions.pop(session_id, None)
        if sh is None:
            return 0
        for rid in list(sh.active_rids):
            w = self._by_request.get(rid)
            if w is not None and not w.cancel.done():
                w.cancel.set_result(None)
        return 1

    async def deploy(self) -> None:
        self._pod_info = await self._deploy.deploy()
        if self._pod_info is not None and hasattr(self._channel, "on_pod_ready"):
            await self._channel.on_pod_ready(self._id, self._pod_info)  # type: ignore[attr-defined]

    async def delete(self) -> None:
        try:
            if self._deploy.resource_id:
                await self._deploy.delete()
        except Exception as e:  # noqa: BLE001
            logger.warning("deploy delete failed: %s", e)
        self._closed = True
        self._sessions.clear()
        self._by_request.clear()
        self._inflight = 0
        await self._session_router.clear()

    async def close(self) -> None:
        await self.delete()
