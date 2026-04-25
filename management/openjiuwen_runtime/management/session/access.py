# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Access：策略生成 session_id / 并发度 / TTL，交给 ServiceManager（双队列）。"""

import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import (
    IAccess,
    IRequest,
    IResponseParser,
    ISessionStrategy,
    IServiceManager,
    SessionRequestWrapper,
)
from .models import AccessConfig, SessionConfig

logger = get_logger(__name__)


class Access(IAccess):
    def __init__(self, service_manager: IServiceManager) -> None:
        self._service_manager = service_manager
        self._strategy: Optional[ISessionStrategy] = None
        self._response_parser: Optional[IResponseParser] = None
        self._config: Optional[AccessConfig] = None

    async def init(
        self,
        response_parser: IResponseParser,
        strategy: ISessionStrategy,
        config: AccessConfig,
        session_config: SessionConfig,
    ) -> None:
        # 与入口共享的会话维度：并发与 TTL 写入策略，供 handle_session 填充 ISessionRequest
        self._response_parser = response_parser
        self._strategy = strategy
        self._config = config
        strategy._concurrency = session_config.concurrency
        strategy._ttl = session_config.ttl
        await self._service_manager.init(response_parser)
        await self._service_manager.start()
        logger.info(
            "Access 已初始化: user_q=%s sys_q=%s image=%s session_max=%s session_ttl=%s "
            "service_concurrency=%s min_idle=%s max=%s port=%s path=%s service_ttl=%s",
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
            config.service_ttl,
        )
        logger.debug(
            "Access init 完成: message_timeout=%s", getattr(config, "message_timeout", None)
        )

    async def send_message(self, msg: IRequest) -> AsyncIterator[Any]:
        # 1) 未 init 时直接失败并打 error
        if not self._strategy:
            logger.error("Access 未初始化，需先调用 init()")
            return
        if not self._response_parser:
            logger.error("ResponseParser 未设置")
            return

        rid = getattr(msg, "request_id", None)
        logger.info("Access 收到请求: request_id=%s", rid)
        # 2) 策略层：从业务请求解析出 session_id、会话级并发、TTL
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
                    to = self._config.message_timeout if self._config else 30
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
