# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Tick 锁：SET NX EX；持有期间可续期；释放时校验 token。"""

from __future__ import annotations

import asyncio
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

_RENEW_IF_OWNER_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""


class TickLock:
    """执行权短锁；支持后台自动续期（lease）。"""

    def __init__(
        self,
        redis: Any,
        *,
        lock_key: str,
        lock_ttl_sec: int,
        token_prefix: str,
        instance_id: str,
        renew_interval_sec: Optional[float] = None,
    ) -> None:
        self._redis = redis
        self._lock_key = lock_key
        self._lock_ttl_sec = max(int(lock_ttl_sec), 1)
        self._token_prefix = token_prefix
        self._instance_id = instance_id
        # 默认约 TTL/3 续一次，保证业务跑久一点也不会丢锁
        self._renew_interval_sec = (
            float(renew_interval_sec)
            if renew_interval_sec is not None
            else max(0.2, self._lock_ttl_sec / 3.0)
        )
        self._renew_task: Optional[asyncio.Task[Any]] = None
        self._renew_token: Optional[str] = None
        self._lost = False
        # 失锁时 set，供 Runner 中断 on_tick；抢到锁 / 开始续期时 clear
        self._lost_event = asyncio.Event()

    @property
    def lock_key(self) -> str:
        return self._lock_key

    @property
    def lost(self) -> bool:
        return self._lost

    @property
    def lost_event(self) -> asyncio.Event:
        return self._lost_event

    def new_token(self) -> str:
        return f"{self._token_prefix}:{self._instance_id}:{uuid4()}"

    def _mark_lost(self, reason: str, token: str) -> None:
        self._lost = True
        self._lost_event.set()
        logger.warning(
            "tick lock lost: key=%s token=%s reason=%s",
            self._lock_key,
            token,
            reason,
        )

    async def try_acquire(self, token: Optional[str] = None) -> Optional[str]:
        """抢锁成功返回 token，失败返回 None。"""
        tok = token or self.new_token()
        try:
            ok = await self._redis.set(self._lock_key, tok, nx=True, ex=self._lock_ttl_sec)
        except asyncio.CancelledError:
            # SET 可能已经成功，await 却因取消没拿到结果：按 token 尽力删掉，避免锁漏到 TTL
            try:
                await self.release_if_owner(tok)
            except Exception:
                logger.exception(
                    "tick lock rollback after cancel failed: key=%s token=%s",
                    self._lock_key,
                    tok,
                )
            raise
        if ok:
            self._lost = False
            self._lost_event.clear()
            logger.info("tick lock acquired: key=%s token=%s", self._lock_key, tok)
            return tok
        logger.debug("tick lock miss: key=%s", self._lock_key)
        return None

    async def renew_once(self, token: str) -> bool:
        """仍是 owner 则续 TTL，返回 True；失锁返回 False。"""
        result = await self._redis.eval(
            _RENEW_IF_OWNER_LUA,
            1,
            self._lock_key,
            token,
            str(self._lock_ttl_sec),
        )
        return int(result or 0) == 1

    def start_renew(self, token: str) -> None:
        """启动后台续期；同一把锁只跑一个续期任务。"""
        self.stop_renew()
        self._renew_token = token
        self._lost = False
        self._lost_event.clear()
        self._renew_task = asyncio.create_task(
            self._renew_loop(token),
            name=f"tick-lock-renew-{self._lock_key}",
        )

    def stop_renew(self) -> None:
        """停止后台续期（不放锁）。"""
        task = self._renew_task
        self._renew_task = None
        self._renew_token = None
        if task is not None and not task.done():
            task.cancel()

    async def _renew_loop(self, token: str) -> None:
        try:
            while True:
                await asyncio.sleep(self._renew_interval_sec)
                try:
                    ok = await self.renew_once(token)
                except asyncio.CancelledError:
                    logger.debug(
                        "tick lock renew cancelled: key=%s token=%s",
                        self._lock_key,
                        token,
                    )
                    raise
                except Exception:
                    # Redis 抖动等：视为失锁，避免续期挂掉后业务还以为握着锁
                    logger.exception(
                        "tick lock renew error: key=%s token=%s",
                        self._lock_key,
                        token,
                    )
                    self._mark_lost("renew_error", token)
                    return
                if not ok:
                    self._mark_lost("renew_not_owner", token)
                    return
                logger.debug("tick lock renewed: key=%s", self._lock_key)
        except asyncio.CancelledError:
            return

    async def release_if_owner(self, token: str) -> bool:
        """先停续期并等到续期任务结束，再仅当仍是自己的 token 时删除锁。"""
        task = self._renew_task
        self._renew_task = None
        self._renew_token = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        result = await self._redis.eval(_RELEASE_IF_OWNER_LUA, 1, self._lock_key, token)
        released = int(result or 0) == 1
        if released:
            logger.debug("tick lock released: key=%s token=%s", self._lock_key, token)
        else:
            logger.debug(
                "tick lock not released (not owner or gone): key=%s token=%s",
                self._lock_key,
                token,
            )
        return released
