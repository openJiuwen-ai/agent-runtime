# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""单实例（Pod）：服务级并发按 session 预留额度、消息级在途计数、发送端点与下行通道。

解耦后 ServiceHandler 不再创建/持有 SessionHandler 实例（归属权在 SessionRegistry），
仅负责：quota 预留、inflight 计数、``ISendEndpoint.send_message``、下行分片、Pod 生命周期。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import (
    IResponseParser,
    ISendEndpoint,
    IServiceHandler,
    IServiceMessageChannel,
    SessionRequestWrapper,
)
from .runtime import IDeployController, NoOpDeployController

logger = get_logger(__name__)

__all__ = ("ServiceHandler",)


class ServiceHandler(IServiceHandler, ISendEndpoint):
    """单个 Pod 的管理者 + 发送端点。

    * 实现 :class:`ISendEndpoint`（``send_message`` / ``endpoint_id`` / ``inflight``），
      供 SessionHandler 跨多端点路由调用；
    * 实现 :class:`IServiceHandler`（quota / 生命周期 / idle 钩子），供 ServiceManager 管理 Pod 池；
    * **不再** 创建或持有 SessionHandler 实例，**不再** 实现 ``handle_message``
      （消息入口由 SessionRuntimeManager → SessionHandler 承担）。
    """

    def __init__(
            self,
            service_id: Optional[str] = None,
            *,
            total_concurrency: int = 200,
            message_channel: IServiceMessageChannel,
            response_parser: IResponseParser,
            deploy_controller: Optional[IDeployController] = None,
            service_template: Optional[Dict[str, Any]] = None,
    ) -> None:
        if total_concurrency <= 0:
            raise ValueError("total_concurrency must be positive")
        self._id = service_id or str(uuid.uuid4())
        self._total = total_concurrency
        # 从 service_template 中提取 service_ttl（可能为 None，表示使用默认值）
        self._service_ttl: Optional[int] = self._extract_service_ttl_from_template(service_template)
        # session_id -> 已预留的服务级额度（等于该 session 声明的 session_concurrency）
        self._session_reserved: Dict[str, int] = {}
        # 通道上尚未完成回调的请求数（消息粒度），用于 idle / session 延期清理
        self._inflight = 0
        self._channel = message_channel
        self._parser = response_parser
        self._deploy: IDeployController = deploy_controller or NoOpDeployController()
        self._by_request: Dict[str, SessionRequestWrapper] = {}
        self._pod_info: Any = None
        self._closed = False
        # ServiceManager 注入: 每次 inflight 归零后被回调一次, Manager 据此推动到期 session 清理与 service_ttl 计时
        self._idle_pool_hook: Optional[Callable[[str], Awaitable[None]]] = None
        # ServiceManager 注入: send_message 失败时 (WSS 重连重试仍失败/通道异常) 被回调,
        # Manager 据此把本实例从 in_use 池摘除、触发 session 侧 endpoint 摘除 + delete pod,
        # 让下一条消息路由到其他健康 Pod (典型场景: agentserver event loop 被同步 tool 堵死,
        # WSS keepalive ping timeout 后无法恢复)。
        self._unhealthy_hook: Optional[Callable[[str, str], Awaitable[None]]] = None
        # WebSocket 等通道在绑定后可拿到本实例与 IResponseParser，供接收循环多路分片
        if hasattr(self._channel, "bind_handler"):
            self._channel.bind_handler(self, self._parser)  # type: ignore[union-attr]
        logger.debug(
            "ServiceHandler 构造: service_id=%s total_concurrency=%s service_ttl=%s",
            self._id, self._total, self._service_ttl,
        )

    @staticmethod
    def _extract_service_ttl_from_template(
            service_template: Optional[Dict[str, Any]]
    ) -> Optional[int]:
        """从 service_template 中提取 service_ttl。如果不存在或无效，返回 None（表示使用默认值）。"""
        if service_template is None:
            return None
        ttl = service_template.get("service_ttl")
        if ttl is None:
            return None
        try:
            ttl_int = int(ttl)
            return ttl_int if ttl_int >= 0 else None
        except (TypeError, ValueError):
            return None

    # ==================== ISendEndpoint ====================

    @property
    def endpoint_id(self) -> str:
        return self._id

    @property
    def inflight(self) -> int:
        """ISendEndpoint.inflight：当前端点在途请求数（同 inflight_requests）。"""
        return self._inflight

    async def send_message(self, wrapper: SessionRequestWrapper) -> None:
        """ISendEndpoint.send_message：登记在途请求并发通道（不占服务额度）。

        由 SessionHandler 在取得 session 信号量后调用。此处
        ``await self._channel.send(...)`` 为 ``WSServiceMessageChannel`` 等
        ``IServiceMessageChannel`` 实现的业务上行入口。
        """
        self._inflight += 1
        rid = wrapper.session_request.request_id
        if rid:
            self._by_request[rid] = wrapper
        logger.debug(
            "send_message 开始: service_id=%s channel_inflight=%s reserved_sessions=%s request_id=%s",
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
                "send_message 完成释放并发: service_id=%s inflight=%s request_id=%s",
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
            # 通知 ServiceManager 把本实例标记为不健康并摘除: 让后续消息路由到其他健康 Pod。
            # 用 create_task 异步触发, 避免阻塞当前异常路径; hook 内部有 _deleting_services 防重入。
            hook = self._unhealthy_hook
            if hook is not None and not self._closed:
                reason = f"{type(e).__name__}: {e}"
                try:
                    asyncio.get_running_loop().create_task(hook(self._id, reason))
                    logger.info(
                        "已通知 ServiceManager 标记不健康: service_id=%s reason=%s",
                        self._id, reason,
                    )
                except Exception as hook_err:  # noqa: BLE001
                    logger.error(
                        "标记不健康钩子调用失败: service_id=%s err=%s",
                        self._id, hook_err, exc_info=True,
                    )
            raise

    # 兼容别名：原 invoke_channel 改名为 send_message，保留旧名一个版本便于过渡
    async def invoke_channel(self, wrapper: SessionRequestWrapper) -> None:
        """deprecated 别名，等价于 :meth:`send_message`。"""
        await self.send_message(wrapper)

    # ==================== IServiceHandler：身份与容量 ====================

    @property
    def id(self) -> str:
        return self._id

    @property
    def service_ttl(self) -> Optional[int]:
        """获取服务实例的 TTL（从 template 提取）。可能为 None，表示应由调用方使用默认值。"""
        return self._service_ttl

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

    @property
    def active_session_count(self) -> int:
        """当前实例上仍预留额度的 session 数（quota 计数，用于 idle/TTL 判定）。"""
        return len(self._session_reserved)

    def open_session_ids(self) -> list[str]:
        """当前实例上仍预留额度的 session_id 列表。"""
        return list(self._session_reserved.keys())

    def try_reserve_session_quota(self, session_id: str, quota: int) -> bool:
        """为尚未预留的 session 占用服务额度；已预留则幂等成功。非 async，供编排层持锁调用。"""
        if session_id in self._session_reserved:
            return True
        need = max(1, int(quota))
        if self.available_concurrency < need:
            return False
        self._session_reserved[session_id] = need
        return True

    @property
    def pod_info(self) -> Any:
        return self._pod_info

    def set_idle_pool_transition_hook(self, hook: Optional[Callable[[str], Awaitable[None]]]) -> None:
        """设置空闲池转换钩子（由 ServiceManager 调用，每次 inflight 归零后回调一次）。"""
        self._idle_pool_hook = hook

    def set_unhealthy_hook(
            self, hook: Optional[Callable[[str, str], Awaitable[None]]]
    ) -> None:
        """设置不健康通知钩子（由 ServiceManager 调用，send_message 失败时回调一次）。

        约定 hook 签名: ``async def hook(service_id: str, reason: str) -> None``。
        ServiceManager 在收到回调后, 会把本实例从 in_use 池摘除、触发 session 侧
        endpoint 摘除、并 delete 底层 pod, 让后续消息路由到其他健康 Pod。
        """
        self._unhealthy_hook = hook

    # ==================== IServiceHandler：session 驱逐（pod-local）====================

    async def evict_session(self, session_id: str) -> int:
        """释放该 session 在本实例的预留额度，并取消本实例上该 session 的在途请求。

        解耦后取代原 ``remove_session``：只处理 pod-local 状态（quota + 取消），
        不销毁 SessionHandler 实例（后者由 SessionRegistry 负责）。

        Returns:
            1 表示该 session 曾在本实例预留额度（已驱逐）；0 表示未找到（无操作）。
            与旧 ``remove_session`` 返回值语义一致，供调用方判断是否真正驱逐。
        """
        removed = self._session_reserved.pop(session_id, None) is not None
        cancelled = 0
        for rid in list(self._by_request.keys()):
            w = self._by_request.get(rid)
            if w is None:
                continue
            if w.session_request.session_id == session_id and not w.cancel.done():
                w.cancel.set_result(None)
                cancelled += 1
        logger.info(
            "evict_session: service_id=%s session_id=%s removed=%s 取消在途请求数=%s 剩余sessions=%s",
            self._id, session_id, removed, cancelled, len(self._session_reserved),
        )
        return 1 if removed else 0

    # 兼容别名：保留旧名一个版本便于过渡
    async def remove_session(self, session_id: str) -> int:
        """deprecated 别名，等价于 :meth:`evict_session`。"""
        return await self.evict_session(session_id)

    # ==================== 下行分片 ====================

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

    # ==================== Pod 生命周期 ====================

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
        delete_error: Exception | None = None
        try:
            if self._deploy.resource_id:
                await self._deploy.delete()
        except Exception as e:  # noqa: BLE001
            logger.error("deploy 后端 delete 失败: service_id=%s err=%s", self._id, e, exc_info=True)
            delete_error = e
        self._closed = True
        self._by_request.clear()
        self._inflight = 0
        self._session_reserved.clear()
        logger.info("ServiceHandler 已销毁: service_id=%s", self._id)
        if delete_error is not None:
            raise delete_error

    async def close(self) -> None:
        await self.delete()
