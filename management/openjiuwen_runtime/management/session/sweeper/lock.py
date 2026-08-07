# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""轻量选主锁：SET NX EX，不续期；释放时校验 token。"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from openjiuwen_runtime.foundation.log import get_logger

logger = get_logger(__name__)

_RELEASE_IF_OWNER_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class SweepLock:
    """每 tick 抢一次的短锁。"""

    def __init__(
        self,
        redis: Any,
        *,
        lock_key: str,
        lock_ttl_sec: int,
        token_prefix: str,
        instance_id: str,
    ) -> None:
        self._redis = redis
        self._lock_key = lock_key
        self._lock_ttl_sec = max(int(lock_ttl_sec), 1)
        self._token_prefix = token_prefix
        self._instance_id = instance_id

    def new_token(self) -> str:
        return f"{self._token_prefix}:{self._instance_id}:{uuid4()}"

    async def try_acquire(self, token: Optional[str] = None) -> Optional[str]:
        """抢锁成功返回 token，失败返回 None。"""
        tok = token or self.new_token()
        ok = await self._redis.set(self._lock_key, tok, nx=True, ex=self._lock_ttl_sec)
        if ok:
            logger.info("sweep lock acquired: key=%s token=%s", self._lock_key, tok)
            return tok
        logger.debug("sweep lock miss: key=%s", self._lock_key)
        return None

    async def release_if_owner(self, token: str) -> bool:
        """仅当仍是自己的 token 时删除锁。"""
        result = await self._redis.eval(_RELEASE_IF_OWNER_LUA, 1, self._lock_key, token)
        released = int(result or 0) == 1
        if released:
            logger.debug("sweep lock released: key=%s token=%s", self._lock_key, token)
        else:
            logger.debug(
                "sweep lock not released (not owner or gone): key=%s token=%s",
                self._lock_key,
                token,
            )
        return released
