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
    def __init__(self, service_manager: IServiceManager):
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
        self._response_parser = response_parser
        self._strategy = strategy
        self._config = config
        strategy._concurrency = session_config.concurrency
        strategy._ttl = session_config.ttl
        await self._service_manager.init(response_parser)
        await self._service_manager.start()
        logger.info(
            "Access initialized: user_q=%s sys_q=%s image=%s session_max=%s session_ttl=%s "
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

    async def send_message(self, msg: IRequest) -> AsyncIterator[Any]:
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
        await self._service_manager.handle_message(wrapper)
        try:
            while True:
                try:
                    data = await asyncio.wait_for(
                        response_queue.get(),
                        timeout=self._config.message_timeout if self._config else 30,
                    )
                except asyncio.TimeoutError:
                    break
                if self._response_parser.is_completed(data):
                    yield self._response_parser.response(data)
                    break
                if cancel.done():
                    break
                yield self._response_parser.response(data)
        finally:
            if not cancel.done():
                cancel.set_result(None)
