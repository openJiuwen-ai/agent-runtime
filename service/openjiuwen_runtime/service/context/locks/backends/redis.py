# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Redis ``SET NX PX`` lock backend with owner-checked Lua mutations."""

from __future__ import annotations

import json
import math
import time
from typing import Any
from uuid import uuid4

from redis.exceptions import ResponseError, WatchError

from ....errors import InvalidLockLease, LockBackendUnavailable, LockLost
from ..base import LockCapabilities, LockCredential

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


class RedisLockBackend:
    """Single-attempt Redis lock operations.

    The stored value is an opaque token containing diagnostic ownership fields.
    Lua compares that complete value, so an expired owner can never delete or
    renew a subsequent owner's lock.
    """

    capabilities = LockCapabilities(distributed=True, fencing=False)

    def __init__(
        self,
        redis: Any,
        *,
        prefix: str = "lock",
        instance_id: str | None = None,
        request_id: str | None = None,
        owns_redis: bool = False,
    ) -> None:
        self._redis = redis
        self.prefix = prefix
        self.instance_id = instance_id
        self.request_id = request_id
        self._owns_redis = owns_redis
        self._closed = False

    def format_key(self, key: str) -> str:
        return f"{self.prefix}:{key}" if self.prefix else key

    @staticmethod
    def _ttl_ms(ttl: float) -> int:
        ttl = float(ttl)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be a finite positive number")
        return max(1, int(ttl * 1000))

    def _token(self) -> str:
        return json.dumps(
            {
                "token": uuid4().hex,
                "instance_id": self.instance_id,
                "request_id": self.request_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    async def try_acquire(self, key: str, ttl: float) -> LockCredential | None:
        self._ensure_open()
        full_key = self.format_key(key)
        token = self._token()
        ttl_ms = self._ttl_ms(ttl)
        acquired_at = time.monotonic()
        if await self._redis.set(full_key, token, nx=True, px=ttl_ms):
            return LockCredential(
                key=full_key,
                token=token,
                backend="redis",
                lease_id=None,
                fencing_token=None,
                acquired_at=acquired_at,
                expires_at=time.monotonic() + float(ttl),
            )
        return None

    async def renew(self, credential: LockCredential, ttl: float) -> LockCredential:
        self._ensure_open()
        self._validate_credential(credential)
        result = await self._cas(credential, _RENEW_SCRIPT, self._ttl_ms(ttl))
        if not result:
            raise LockLost(
                f"lock {credential.key!r} is no longer owned by this credential"
            )
        return credential.renewed(float(ttl))

    async def release(self, credential: LockCredential) -> bool:
        self._ensure_open()
        self._validate_credential(credential)
        return bool(await self._cas(credential, _RELEASE_SCRIPT))

    async def ping(self) -> bool:
        self._ensure_open()
        return bool(await self._redis.ping())

    async def close(self) -> None:
        self._closed = True
        if self._owns_redis:
            await self._redis.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise LockBackendUnavailable("Redis lock backend is closed")

    @staticmethod
    def _validate_credential(credential: LockCredential) -> None:
        if credential.backend != "redis":
            raise InvalidLockLease(
                f"credential backend {credential.backend!r} cannot be used with redis"
            )

    async def _cas(self, credential: LockCredential, script: str, *args: Any) -> int:
        try:
            return int(
                await self._redis.eval(
                    script, 1, credential.key, credential.token, *args
                )
            )
        except ResponseError as exc:
            # Older fakeredis builds omit Lua support. Keep the compatibility
            # test path while real Redis always takes the atomic Lua branch.
            if (
                "unknown command" not in str(exc).lower()
                or "eval" not in str(exc).lower()
            ):
                raise
            return int(await self._watch_cas(credential, script, *args))

    async def _watch_cas(
        self, credential: LockCredential, script: str, *args: Any
    ) -> int:
        pipe = self._redis.pipeline(transaction=True)
        try:
            await pipe.watch(credential.key)
            current = await pipe.get(credential.key)
            if _as_bytes(current) != credential.token.encode():
                await pipe.unwatch()
                return 0
            pipe.multi()
            if script is _RENEW_SCRIPT:
                pipe.pexpire(credential.key, args[0])
            else:
                pipe.delete(credential.key)
            response = await pipe.execute()
            return int(response[0]) if response else 0
        except WatchError:
            try:
                await pipe.unwatch()
            except Exception:  # noqa: BLE001
                pass
            return 0


def _as_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    return value.encode() if isinstance(value, str) else bytes(value)


__all__ = ["RedisLockBackend"]
