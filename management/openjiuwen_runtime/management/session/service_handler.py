# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""单实例：服务级并发按 session 预留 session_concurrency、消息级在 invoke_channel 计数、session 子处理器与下行通道。"""

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
    """每个 session 首次占用时在本实例上预留 ``session_concurrency`` 的服务额度；
    同 session 的后续消息不再增加预留，仅受 SessionHandler 内并发限制。
    通道在途请求数见 ``inflight_requests``（用于 TTL / idle 钩子），与服务额度无关。"""

    def __init__(
        self,
        service_id: Optional[str] = None,
        *,
        total_concurrency: int = 200,
        message_channel: IServiceMessageChannel,
        response_parser: IResponseParser,
        deploy_controller: Optional[IDeployController] = None,
        generation: int = 0,
    ) -> None:
        if total_concurrency <= 0:
            raise ValueError("total_concurrency must be positive")
        self._id = service_id or str(uuid.uuid4())
        self._total = total_concurrency
        self._generation = generation
        # session_id -> 已预留的服务级额度（等于该 session 声明的 session_concurrency）
        self._session_reserved: Dict[str, int] = {}
        self._session_create_lock = asyncio.Lock()
        # 通道上尚未完成回调的请求数（消息粒度），用于 idle / session 延期清理
        self._inflight = 0
        self._channel = message_channel
        self._parser = response_parser
        self._deploy: IDeployController = deploy_controller or NoOpDeployController()
        self._session_router = SessionRouter()
        self._sessions: Dict[str, SessionHandler] = {}
        self._by_request: Dict[str, SessionRequestWrapper] = {}
        self._pod_info: Any = None
        self._closed = False
        # ServiceManager 注入: 每次 inflight 归零后被回调一次, Manager 据此推动到期 session 清理与 service_ttl 计时
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
        used = sum(self._session_reserved.values())
        return max(0, self._total - used)

    @property
    def inflight_requests(self) -> int:
        return self._inflight

    def try_reserve_session_quota(self, session_id: str, quota: int) -> bool:
        """为尚未预留的 session 占用服务额度；已预留则幂等成功。非 async，供 ServiceManager 持锁调用。"""
        if session_id in self._session_reserved:
            return True
        need = max(1, int(quota))
        if self.available_concurrency < need:
            return False
        self._session_reserved[session_id] = need
        return True

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)

    def open_session_ids(self) -> list[str]:
        return list(self._sessions.keys())

    def session_active_request_count(self, session_id: str) -> int:
        sh = self._sessions.get(session_id)
        return len(sh.active_rids) if sh else 0

    @property
    def pod_info(self) -> Any:
        return self._pod_info

    def set_idle_pool_transition_hook(
        self, hook: Optional[Callable[[str], Awaitable[None]]]
    ) -> None:
        """由 ServiceManager 设置: 每次 inflight 归零后回调一次, 由 Manager 自行判断
        是否要 flush 已到期 session、是否要 arm service_ttl。"""
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

        # 每 session 一个 SessionHandler；额度由 ServiceManager 持锁预留，或直接调用本实例时在此补齐
        async with self._session_create_lock:
            sh = self._sessions.get(session_id)
            if sh is None:
                if session_id not in self._session_reserved:
                    if not self.try_reserve_session_quota(session_id, max_sess):
                        raise RuntimeError(
                            f"service_id={self._id} 无足够并发为新 session 预留 "
                            f"(session_concurrency={max_sess}, available={self.available_concurrency})"
                        )
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
        """SessionHandler 在取得 session 信号量后调用：登记在途请求并发通道（不占服务额度）。

        此处 ``await self._channel.send(...)`` 为
        ``WSServiceMessageChannel`` 等 ``IServiceMessageChannel`` 实现的业务上行入口。
        """
        self._inflight += 1
        rid = wrapper.session_request.request_id
        if rid:
            self._by_request[rid] = wrapper
        logger.debug(
            "invoke_channel 开始: service_id=%s channel_inflight=%s reserved_sessions=%s request_id=%s",
            self._id,
            self._inflight,
            len(self._session_reserved),
            rid,
        )

        async def _complete(r: Optional[str]) -> None:
            self._inflight = max(0, self._inflight - 1)
            if r:
                self._by_request.pop(r, None)
            logger.debug(
                "invoke_channel 完成: service_id=%s channel_inflight=%s request_id=%s",
                self._id,
                self._inflight,
                r,
            )
            hook = self._idle_pool_hook
            # inflight 归零即回调一次, 由 Manager 检查 pending session 与是否 arm service_ttl
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
            self._session_reserved.pop(session_id, None)
            return 0
        self._session_reserved.pop(session_id, None)
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
        self._session_reserved.clear()
        self._inflight = 0
        await self._session_router.clear()
        logger.info("ServiceHandler 已销毁: service_id=%s", self._id)

    async def close(self) -> None:
        await self.delete()
