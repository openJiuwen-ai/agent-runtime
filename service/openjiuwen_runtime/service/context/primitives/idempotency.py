# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""幂等（设计 §9.2）。

- ``Idempotency.acquire(request_id, window)``：``SETNX`` 去重；返回 guard。
- ``idempotency_guard(window, mode)``：中间件。``reject``（默认，重复 → idempotent 错误）/
  ``cache``（回放首次成功结果）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from ...envelope import Envelope, ResponseEnvelope
from ...routing.result import UnaryResult


@dataclass
class IdempotencyGuard:
    """``acquire`` 的结论。``acquired=False`` 时 ``cached_result`` 可能非空（cache 模式回放）。"""

    acquired: bool
    cached_result: Optional[ResponseEnvelope] = None
    _idem: Optional["Idempotency"] = None
    _request_id: Optional[str] = None
    _window: int = 60

    async def succeed(self, result: ResponseEnvelope) -> None:
        """成功后缓存结果（供后续重复请求回放）。"""
        if self._idem is not None and self._request_id is not None:
            await self._idem._store_result(self._request_id, result, self._window)


class Idempotency:
    def __init__(self, redis: Any, prefix: str = "idem") -> None:
        self._redis = redis
        self._prefix = prefix

    def _owned_key(self, request_id: str) -> str:
        return f"{self._prefix}:req:{request_id}"

    def _result_key(self, request_id: str) -> str:
        return f"{self._prefix}:res:{request_id}"

    async def acquire(self, request_id: str, window: int = 60) -> IdempotencyGuard:
        owned = await self._redis.set(self._owned_key(request_id), "1", nx=True, ex=window)
        if owned:
            return IdempotencyGuard(acquired=True, cached_result=None,
                                    _idem=self, _request_id=request_id, _window=window)
        raw = await self._redis.get(self._result_key(request_id))
        cached: ResponseEnvelope | None = None
        if raw is not None:
            text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            cached = ResponseEnvelope.from_dict(json.loads(text))
        return IdempotencyGuard(acquired=False, cached_result=cached)

    async def _store_result(self, request_id: str, result: ResponseEnvelope, window: int) -> None:
        await self._redis.set(
            self._result_key(request_id), json.dumps(result.to_dict()), ex=window)


def idempotency_guard(window: int = 60, mode: str = "reject"):
    """幂等中间件工厂。

    - ``reject``（默认）：重复 request_id → ``idempotent`` 错误信封。
    - ``cache``：重复 request_id → 回放首次成功结果；handler 不再执行。
    """

    async def middleware(ctx: Any, env: Envelope, nxt) -> Any:
        guard = await ctx.idempotency.acquire(env.metadata.request_id, window=window)
        if not guard.acquired:
            if mode == "cache" and guard.cached_result is not None:
                return UnaryResult(response=guard.cached_result)
            return UnaryResult(response=_idempotent_error(env))
        result = await nxt(ctx, env)
        if mode == "cache" and isinstance(result, UnaryResult) and result.response.ok:
            await guard.succeed(result.response)
        return result

    return middleware


def _idempotent_error(env: Envelope) -> ResponseEnvelope:
    return ResponseEnvelope(
        type=env.type, metadata=env.metadata, rawdata={}, ok=False,
        error_code="idempotent", error_message=f"duplicate request_id {env.metadata.request_id!r}")
