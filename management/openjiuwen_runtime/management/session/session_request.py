# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from typing import Any, Optional, TYPE_CHECKING

from .interfaces import IRequest, ISessionRequest

if TYPE_CHECKING:
    from .models import MessagePriority


class SessionRequest(ISessionRequest):
    """ISessionRequest 的通用实现"""

    def __init__(
        self,
        session_id: str,
        concurrency: int,
        ttl: int,
        request_id: Optional[str],
        raw: IRequest,
    ):
        self._session_id = session_id
        self._concurrency = concurrency
        self._ttl = ttl
        self._request_id = request_id
        self._raw = raw

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def session_concurrency(self) -> int:
        return self._concurrency

    @property
    def session_ttl(self) -> int:
        return self._ttl

    @property
    def request_id(self) -> Optional[str]:
        return self._request_id

    @property
    def raw_msg(self) -> Any:
        return self._raw

    @property
    def priority(self) -> "MessagePriority":
        from .models import MessagePriority
        return MessagePriority.MEDIUM
