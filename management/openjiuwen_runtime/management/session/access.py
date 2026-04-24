# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Access 入口实现 - 调用 session 策略将 IRequest 转换为 ISessionRequest，路由到 ServiceManager"""

import asyncio
from typing import Any, AsyncIterator, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import IAccess, IRequest, IResponseParser, ISessionStrategy, IServiceManager, SessionRequestWrapper
from .models import AccessConfig

logger = get_logger(__name__)


class Access(IAccess):
    """消息统一入口，内部调用 session 策略，把 IRequest 转换为 ISessionRequest"""

    def __init__(self, service_manager: IServiceManager):
        self._service_manager = service_manager
        self._strategy: Optional[ISessionStrategy] = None
        self._response_parser: Optional[IResponseParser] = None
        self._config: Optional[AccessConfig] = None

    def init(self, response_parser: IResponseParser, strategy: ISessionStrategy, config: AccessConfig):
        self._response_parser = response_parser
        self._strategy = strategy
        self._config = config
        self._service_manager.init(response_parser)
        strategy._concurrency = config.max_concurrency
        strategy._ttl = config.service_ttl
        logger.info(
            f"Access initialized: concurrency={config.max_concurrency}, "
            f"ttl={config.service_ttl}s, "
            f"queue_size={config.queue_size}, "
            f"message_timeout={config.message_timeout}s"
        )

    async def send_message(self, msg: IRequest) -> AsyncIterator[Any]:
        """将 IRequest 通过策略转换为 ISessionRequest，路由到 ServiceManager，流式返回响应"""
        if not self._strategy:
            logger.error("Access not initialized, call init() first")
            return
        if not self._response_parser:
            logger.error("ResponseParser not initialized")
            return

        session_request = self._strategy.handle_session(msg)

        response_queue: asyncio.Queue[Any] = asyncio.Queue()
        cancel: asyncio.Future = asyncio.get_running_loop().create_future()
        wrapper = SessionRequestWrapper(session_request, response_queue, cancel)

        self._service_manager.handle_message(wrapper)
        logger.debug(
            f"Message dispatched: session_id='{session_request.session_id}', "
            f"request_id='{session_request.request_id}'"
        )

        try:
            while True:
                try:
                    data = await asyncio.wait_for(
                        response_queue.get(),
                        timeout=self._config.message_timeout if self._config else 30,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Response timeout: session_id='{session_request.session_id}', "
                        f"request_id='{session_request.request_id}'"
                    )
                    break

                if cancel.done():
                    logger.debug(f"Message cancelled: session_id='{session_request.session_id}'")
                    break

                if self._response_parser.is_completed(data):
                    yield self._response_parser.response(data)
                    break

                yield self._response_parser.response(data)
        finally:
            if not cancel.done():
                cancel.set_result(None)
            logger.debug(
                f"Message stream ended: session_id='{session_request.session_id}', "
                f"request_id='{session_request.request_id}'"
            )
