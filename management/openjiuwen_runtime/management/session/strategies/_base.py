# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from openjiuwen_runtime.foundation.log import get_logger

from ..interfaces import IRequest, ISessionRequest, ISessionStrategy
from ..session_request import SessionRequest

logger = get_logger(__name__)


class BaseSessionStrategy(ISessionStrategy):
    """策略基类，子类只需实现 _build_key，生成稳定 session 键与 ISessionRequest。"""

    _concurrency: int = 1
    _ttl: int = 300

    def configure(self, concurrency: int, ttl: int) -> None:
        """配置会话的并发度和 TTL"""
        self._concurrency = concurrency
        self._ttl = ttl

    def _build_key(self, msg: IRequest) -> str:
        raise NotImplementedError

    def handle_session(self, msg: IRequest) -> ISessionRequest:
        # 从 IRequest 聚合成带并发/TTL/原始消息的 ISessionRequest
        sid = self._build_key(msg)
        if not sid.strip():
            logger.warning("策略生成的 session 键为空或全空白, request_id=%s", msg.request_id)
        logger.debug("策略 handle_session: session_id=%s conc=%s ttl=%s", sid, self._concurrency, self._ttl)
        return SessionRequest(
            service_id=sid,
            concurrency=self._concurrency,
            ttl=self._ttl,
            request_id=msg.request_id,
            raw=msg,
        )
