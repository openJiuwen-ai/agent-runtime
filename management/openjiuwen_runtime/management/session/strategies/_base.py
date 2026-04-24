# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from ..interfaces import IRequest, ISessionRequest, ISessionStrategy
from ..session_request import SessionRequest


class BaseSessionStrategy(ISessionStrategy):
    """策略基类，子类只需实现 _build_key"""

    _concurrency: int = 1
    _ttl: int = 300

    def _build_key(self, msg: IRequest) -> str:
        raise NotImplementedError

    def handle_session(self, msg: IRequest) -> ISessionRequest:
        return SessionRequest(
            session_id=self._build_key(msg),
            concurrency=self._concurrency,
            ttl=self._ttl,
            request_id=msg.request_id,
            raw=msg,
        )
