# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Access：策略生成 session_id / 并发度 / TTL，交给 ServiceManager（双队列）。"""

import asyncio
import uuid
from typing import Any, AsyncIterator, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import (
    IAccess,
    IRequest,
    IResponseParser,
    ISessionStrategy,
    IServiceManager,
    SessionRequestWrapper, ISessionRequest,
)
from .models import AccessConfig, SessionConfig

logger = get_logger(__name__)


class _AutoIdRequest(IRequest):
    """覆盖 ``request_id`` 为 Access 生成的 UUID，其余字段透传给原始 ``IRequest``。

    用途：当用户 ``IRequest.request_id`` 为空时，Access 自动生成；下游
    ``WSServiceMessageChannel`` 依靠 ``request_id`` 做多路复用，否则会拒收。
    """

    def __init__(self, base: IRequest, rid: str) -> None:
        self._base = base
        self._rid = rid

    @property
    def request_id(self) -> Optional[str]:
        return self._rid

    @property
    def chat_id(self) -> Optional[str]:
        return self._base.chat_id

    @property
    def bot_id(self) -> Optional[str]:
        return self._base.bot_id

    @property
    def user_id(self) -> Optional[str]:
        return self._base.user_id

    @property
    def session_id(self) -> Optional[str]:
        return self._base.session_id

    @property
    def wire_dict(self) -> Any:
        # 让 ws_client_channel._to_jsonable 能拿到含 request_id 的上行字典；
        # 若原对象没有 wire_dict，返回 None 走默认序列化分支。
        wd = getattr(self._base, "wire_dict", None)
        if isinstance(wd, dict):
            return wd
        return None


class Access(IAccess):
    def __init__(self, service_manager: IServiceManager) -> None:
        self._service_manager = service_manager
        self._strategy: Optional[ISessionStrategy] = None
        self._response_parser: Optional[IResponseParser] = None
        self._config: Optional[AccessConfig] = None
        self._shutdown_done: bool = False

    async def init(
            self,
            response_parser: IResponseParser,
            config: AccessConfig,
            session_config: SessionConfig,
            strategy: ISessionStrategy = None,
    ) -> None:
        # 与入口共享的会话维度：并发与 TTL 写入策略，供 handle_session 填充 ISessionRequest
        self._response_parser = response_parser
        self._config = config
        if strategy:
            self._strategy = strategy
            strategy._concurrency = session_config.concurrency
            strategy._ttl = session_config.ttl
        await self._service_manager.init(response_parser)
        await self._service_manager.start()
        logger.info(
            "Access 已初始化: user_q=%s sys_q=%s image=%s session_max=%s session_ttl=%s "
            "service_concurrency=%s min_idle=%s max=%s port=%s path=%s ws_tls=%s service_ttl=%s",
            config.user_queue_size,
            config.system_queue_size,
            config.image,
            session_config.concurrency,
            session_config.ttl,
            config.service_concurrency,
            config.min_idle_services,
            config.max_services,
            config.target_port,
            config.invoke_path,
            config.ws_use_tls,
            config.service_ttl,
        )
        logger.debug(
            "Access init 完成: message_timeout=%s", getattr(config, "message_timeout", None)
        )

    async def shutdown(self) -> None:
        """优雅退出：停 ServiceManager 内全部 asyncio 任务与双队列、取消定时器、delete 已拉起的服务。"""
        if self._shutdown_done:
            logger.debug("Access shutdown 被忽略(幂等): 已关闭")
            return
        self._shutdown_done = True
        await self._service_manager.stop()
        logger.info("Access 已 shutdown")

    async def update_config(
        self, config: AccessConfig, session_config: Optional[SessionConfig] = None
    ) -> None:
        """运行时热更新配置。存量 session/service 不变，新建的使用新值。"""
        self._config = config
        await self._service_manager.update_config(
            min_idle_services=config.min_idle_services,
            max_services=config.max_services,
            service_idle_ttl=config.service_ttl,
            autoscale_interval=config.autoscale_interval,
        )
        if session_config and self._strategy:
            self._strategy._concurrency = session_config.concurrency
            self._strategy._ttl = session_config.ttl
        logger.info("Access 配置已热更新")

    async def send_message(self, msg: IRequest | ISessionRequest) -> AsyncIterator[Any]:
        # 1) 未 init 时直接失败并打 error
        if self._shutdown_done:
            logger.error("Access 已 shutdown，不再收消息")
            return
        if not self._response_parser:
            logger.error("ResponseParser 未设置")
            return
        if isinstance(msg, ISessionRequest):
            session_request = msg
            rid = session_request.request_id
            logger.debug(
                "Access receive session: session_id=%s session_conc=%s session_ttl=%s request_id=%s",
                session_request.session_id,
                session_request.session_concurrency,
                session_request.session_ttl,
                rid,
            )
        else:
            rid = getattr(msg, "request_id", None)
            if not rid:
                rid = uuid.uuid4().hex
                logger.info(
                    "Access 自动生成 request_id=%s（请求未提供，多路复用必须非空）", rid
                )
                wd = getattr(msg, "wire_dict", None)
                if isinstance(wd, dict) and not wd.get("request_id"):
                    # 对端按 request_id 回包路由；inplace 注入到原 wire_dict 即可
                    wd["request_id"] = rid
                msg = _AutoIdRequest(msg, rid)
            logger.info("Access 收到请求: request_id=%s", rid)
            # 2) 策略层：从业务请求解析出 session_id、会话级并发、TTL
            if not self._strategy:
                logger.error("未设置session策略")
                return
            session_request = self._strategy.handle_session(msg)
            logger.debug(
                "Access 策略生成 session: session_id=%s session_conc=%s session_ttl=%s",
                session_request.session_id,
                session_request.session_concurrency,
                session_request.session_ttl,
            )

        # 3) 每个入口请求独占一条响应队列 + cancel，用于多路复用/取消
        response_queue: asyncio.Queue[Any] = asyncio.Queue()
        cancel: asyncio.Future = asyncio.get_running_loop().create_future()
        wrapper = SessionRequestWrapper(session_request, response_queue, cancel)

        # 4) 入用户队列，由 ServiceManager 异步消费并路由到具体服务实例
        await self._service_manager.handle_message(wrapper)
        logger.debug("Access 已将请求投递 ServiceManager, request_id=%s", rid)
        try:
            while True:
                try:
                    to = self._config.message_timeout if self._config else 600
                    data = await asyncio.wait_for(response_queue.get(), timeout=to)
                except asyncio.TimeoutError:
                    # 等响应超时：结束迭代（业务上视为挂起/失败，由调用方处理）
                    logger.error(
                        "Access 等待下游响应超时: request_id=%s timeout=%s", rid, to
                    )
                    break
                if self._response_parser.is_completed(data):
                    logger.debug("Access 收到终态分片, request_id=%s", rid)
                    yield self._response_parser.response(data)
                    break
                if cancel.done():
                    logger.debug("Access 因 cancel 结束收包, request_id=%s", rid)
                    break
                logger.debug("Access 收到流式分片, request_id=%s", rid)
                yield self._response_parser.response(data)
        finally:
            if not cancel.done():
                cancel.set_result(None)
            logger.debug("Access send_message 协程结束, request_id=%s", rid)
