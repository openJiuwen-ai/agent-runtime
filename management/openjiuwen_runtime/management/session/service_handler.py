# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""单实例：服务级并发（单请求=1）、session 子处理器、部署与下行通道。"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

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
        # 服务级并发：与 session 内并发独立，二者都通过 acquire 排队
        self._service_sem = asyncio.BoundedSemaphore(total_concurrency)
        self._inflight = 0
        self._channel = message_channel
        self._parser = response_parser
        self._deploy: IDeployController = deploy_controller or NoOpDeployController()
        self._session_router = SessionRouter()
        self._sessions: Dict[str, SessionHandler] = {}
        self._by_request: Dict[str, SessionRequestWrapper] = {}
        self._pod_info: Any = None
        self._closed = False
        # ServiceManager 注入：in_use 且 session/inflight 均空时，按 service_ttl 转入 idle
        self._idle_pool_hook: Optional[Callable[[str], Awaitable[None]]] = None
        # WebSocket 等通道在绑定后可拿到本实例与 IResponseParser，供接收循环多路分片
        if hasattr(self._channel, "bind_handler"):
            self._channel.bind_handler(self, self._parser)  # type: ignore[union-attr]
        logger.debug("ServiceHandler 构造: service_id=%s total_concurrency=%s", self._id, self._total)

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

    def open_session_ids(self) -> list[str]:
        return list(self._sessions.keys())

    @property
    def pod_info(self) -> Any:
        return self._pod_info

    def set_idle_pool_transition_hook(
        self, hook: Optional[Callable[[str], Awaitable[None]]]
    ) -> None:
        """由 ServiceManager 设置：在 in_use 上最后一次 inflight 结束且无 session 时回调。"""
        self._idle_pool_hook = hook

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    async def handle_message(self, msg: SessionRequestWrapper) -> None:
        if self._closed:
            logger.error("ServiceHandler 已关闭, 拒收: service_id=%s", self._id)
            raise RuntimeError("ServiceHandler is closed")
        sreq = msg.session_request
        session_id = sreq.session_id
        max_sess = sreq.session_concurrency
        if max_sess <= 0:
            logger.error("session_concurrency 非法: %s", max_sess)
            raise ValueError("session_concurrency must be positive")

        # 每 session 一个 SessionHandler, 内用 BoundedSemaphore 做会话内限流
        sh = self._sessions.get(session_id)
        if sh is None:
            sh = SessionHandler(
                session_id, max_sess, self, self._session_router
            )
            self._sessions[session_id] = sh
            logger.info(
                "新 session 子处理器: service_id=%s session_id=%s max_parallel=%s",
                self._id,
                session_id,
                max_sess,
            )
        logger.debug(
            "ServiceHandler 分发到 SessionHandler: service_id=%s session_id=%s request_id=%s",
            self._id,
            session_id,
            sreq.request_id,
        )
        await sh.handle_message(msg)

    async def invoke_channel(self, wrapper: SessionRequestWrapper) -> None:
        """SessionHandler 在取得 session 信号量后调用：再占 1 路服务级并发, 经通道下发给下游。

        此处 ``await self._channel.send(...)`` 为
        ``WSServiceMessageChannel`` 等 ``IServiceMessageChannel`` 实现的业务上行入口。
        """
        await self._service_sem.acquire()
        self._inflight += 1
        rid = wrapper.session_request.request_id
        if rid:
            self._by_request[rid] = wrapper
        logger.debug(
            "invoke_channel 开始: service_id=%s inflight=%s/%s request_id=%s",
            self._id,
            self._inflight,
            self._total,
            rid,
        )

        async def _complete(r: Optional[str]) -> None:
            self._inflight = max(0, self._inflight - 1)
            self._service_sem.release()
            if r:
                self._by_request.pop(r, None)
            logger.debug(
                "invoke_channel 完成释放并发: service_id=%s inflight=%s request_id=%s",
                self._id,
                self._inflight,
                r,
            )
            hook = self._idle_pool_hook
            # 无在途 inflight 即触发「无业务」计时；session 记录可仍在，由 Manager 入 idle 前逐出
            if hook and self._inflight == 0:
                asyncio.get_running_loop().create_task(hook(self._id))

        try:
            await self._channel.send(
                self._id,
                wrapper,
                response_parser=self._parser,
                on_request_complete=_complete,
            )
        except Exception as e:
            logger.error(
                "消息通道 send 失败: service_id=%s request_id=%s err=%s",
                self._id,
                rid,
                e,
                exc_info=True,
            )
            await _complete(rid)
            raise

    async def dispatch_inbound_chunk(
        self, data: dict[str, Any], response_parser: IResponseParser
    ) -> bool:
        """长连接多路复用：按响应中的 request_id 写回对应 response_queue。"""
        rid = response_parser.request_id(data)
        if not rid:
            logger.debug("下行分片无 request_id, 已忽略")
            return False
        w = self._by_request.get(rid)
        if w is None:
            logger.debug("下行分片 request_id 无活跃等待: %s", rid)
            return False
        if w.cancel.done():
            return True
        await w.response_queue.put(data)
        logger.debug("下行分片已写入 response_queue: request_id=%s", rid)
        return True

    async def remove_session(self, session_id: str) -> int:
        sh = self._sessions.pop(session_id, None)
        if sh is None:
            return 0
        logger.info("移除 session: service_id=%s session_id=%s", self._id, session_id)
        for rid in list(sh.active_rids):
            w = self._by_request.get(rid)
            if w is not None and not w.cancel.done():
                w.cancel.set_result(None)
        return 1

    async def deploy(self) -> None:
        self._pod_info = await self._deploy.deploy()
        logger.info("ServiceHandler deploy 完成: service_id=%s pod/资源已就绪", self._id)
        if self._pod_info is not None and hasattr(self._channel, "on_pod_ready"):
            await self._channel.on_pod_ready(self._id, self._pod_info)  # type: ignore[attr-defined]
            logger.debug("已通知 message_channel on_pod_ready")

    async def delete(self) -> None:
        # 先关长连接(WS)再删 Pod, 避免悬挂接收协程
        if hasattr(self._channel, "close"):
            with contextlib.suppress(Exception):
                await self._channel.close()  # type: ignore[union-attr, misc]
        try:
            if self._deploy.resource_id:
                await self._deploy.delete()
        except Exception as e:  # noqa: BLE001
            logger.error("deploy 后端 delete 失败: service_id=%s err=%s", self._id, e, exc_info=True)
        self._closed = True
        self._sessions.clear()
        self._by_request.clear()
        self._inflight = 0
        self._service_sem = asyncio.BoundedSemaphore(self._total)
        await self._session_router.clear()
        logger.info("ServiceHandler 已销毁: service_id=%s", self._id)

    async def close(self) -> None:
        await self.delete()
