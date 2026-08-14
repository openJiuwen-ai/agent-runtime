# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""主备协调：提前报名 → 等到开火点 → 抽签选主 → 持锁续期执行。

流程（每拍，配合 JobRunner 提前 ``gather_window`` 醒来）：
1. ``planned_fire`` 为本拍整点 T；``now`` 多为 T-窗口
2. ``SADD candidates:{epoch}`` 报名（epoch 取自 T）
3. 睡到 T（剩余窗口），让网络慢的实例也能进来
4. Lua 原子抽签：``SRANDMEMBER`` + ``SET NX winner:{epoch}``
5. 只有 winner 去 ``SET NX`` 执行锁，并启动续期；别人空转
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.foundation.periodic.lock import TickLock

logger = get_logger(__name__)

_ELECT_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
    return existing
end
local pick = redis.call('SRANDMEMBER', KEYS[2])
if not pick then
    return false
end
local ok = redis.call('SET', KEYS[1], pick, 'NX', 'EX', tonumber(ARGV[1]))
if ok then
    return pick
end
return redis.call('GET', KEYS[1])
"""


class SingleLeaderCoordinator:
    """主备：开火前窗口内集齐候选人，到点后随机选唯一执行者。"""

    def __init__(
        self,
        redis: Any,
        *,
        lock_key: str,
        lock_ttl_sec: int = 1,
        token_prefix: str = "job",
        instance_id: str = "",
        gather_window_sec: float = 0.08,
        meta_ttl_sec: int = 3,
    ) -> None:
        self._redis = redis
        self._instance_id = instance_id
        self._lock_key = lock_key
        self._gather_window_sec = max(float(gather_window_sec), 0.0)
        self._meta_ttl_sec = max(int(meta_ttl_sec), 1)
        self._lock = TickLock(
            redis,
            lock_key=lock_key,
            lock_ttl_sec=lock_ttl_sec,
            token_prefix=token_prefix,
            instance_id=instance_id,
        )

    def _candidates_key(self, epoch: int) -> str:
        return f"{self._lock_key}:candidates:{epoch}"

    def _winner_key(self, epoch: int) -> str:
        return f"{self._lock_key}:winner:{epoch}"

    async def try_claim(
        self,
        *,
        now: float,
        instance_id: str,
        planned_fire: float | None = None,
    ) -> Optional[str]:
        iid = instance_id or self._instance_id
        fire_at = float(planned_fire) if planned_fire is not None else float(now)
        epoch = int(fire_at)
        cand_key = self._candidates_key(epoch)
        winner_key = self._winner_key(epoch)

        await self._redis.sadd(cand_key, iid)
        try:
            await self._redis.expire(cand_key, self._meta_ttl_sec)
        except Exception:
            logger.debug("candidates expire failed: key=%s", cand_key)

        if planned_fire is not None:
            delay = fire_at - now
        else:
            delay = self._gather_window_sec
        if delay > 0:
            await asyncio.sleep(delay)

        winner = await self._elect(winner_key, cand_key)
        if winner is None:
            logger.debug("no candidates for epoch=%s instance=%s", epoch, iid)
            return None

        winner_s = winner.decode() if isinstance(winner, (bytes, bytearray)) else str(winner)
        if winner_s != iid:
            logger.debug(
                "not elected: epoch=%s instance=%s winner=%s",
                epoch,
                iid,
                winner_s,
            )
            return None

        token = await self._lock.try_acquire()
        if token is None:
            logger.warning(
                "elected but lock busy: epoch=%s instance=%s key=%s",
                epoch,
                iid,
                self._lock_key,
            )
            return None

        self._lock.start_renew(token)
        logger.info(
            "single_leader claimed: epoch=%s instance=%s key=%s",
            epoch,
            iid,
            self._lock_key,
        )
        return token

    async def _elect(self, winner_key: str, cand_key: str) -> Any:
        return await self._redis.eval(
            _ELECT_LUA,
            2,
            winner_key,
            cand_key,
            str(self._meta_ttl_sec),
        )

    async def release(self, token: str) -> None:
        try:
            await self._lock.release_if_owner(token)
        except Exception:
            logger.exception(
                "single_leader release failed: key=%s token=%s",
                self._lock.lock_key,
                token,
            )
