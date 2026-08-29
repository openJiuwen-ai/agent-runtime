# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""主备协调：提前报名 → 等到开火点 → 抽签选主 → 持锁续期执行。

流程（每拍，配合 JobRunner 提前 ``gather_window`` 醒来）：
1. ``planned_fire`` 为本拍整点 T；``now`` 多为 T-窗口
2. Lua ``SADD`` + ``EXPIRE`` 报名（epoch 取自 T，一次写完避免 key 泄漏）
3. 睡到 T（剩余窗口），让网络慢的实例也能进来
4. Lua 原子抽签：``SRANDMEMBER`` + ``SET NX winner:{epoch}``
5. 只有 winner 去 ``SET NX`` 执行锁，并启动续期；别人空转
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Optional

from openjiuwen_runtime.foundation.log import get_logger

from ..clock import RedisAlignedClock, redis_unix_now
from ..lock import TickLock

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

# SADD + EXPIRE 一次完成，避免进程在两步之间崩溃留下没有 TTL 的报名 key
_ENROLL_LUA = """
redis.call('SADD', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
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
        meta_ttl_sec: int | None = None,
        clock: RedisAlignedClock | None = None,
    ) -> None:
        self._redis = redis
        self._instance_id = instance_id
        self._lock_key = lock_key
        self._clock = clock
        self._gather_window_sec = max(float(gather_window_sec), 0.0)
        # 元数据 TTL 至少盖住集合窗口，避免睡醒后 candidates 已过期
        if meta_ttl_sec is None:
            meta_ttl_sec = max(3, int(math.ceil(self._gather_window_sec)) + 2)
        self._meta_ttl_sec = max(int(meta_ttl_sec), 1)
        self._lock = TickLock(
            redis,
            lock_key=lock_key,
            lock_ttl_sec=lock_ttl_sec,
            token_prefix=token_prefix,
            instance_id=instance_id,
        )

    @property
    def lock_lost_event(self) -> asyncio.Event:
        """执行锁失锁事件；Runner 可据此中断 on_tick。"""
        return self._lock.lost_event

    def _candidates_key(self, epoch: int) -> str:
        # hash tag({lock_key})：Redis Cluster 下 candidates 与 winner 落同一
        # slot，抽签 Lua 的双键 EVAL 才能通过 cluster 客户端的同槽校验；
        # 单实例下 {} 无语义。执行锁键本身保持原样（单键操作无需同槽）。
        return f"{{{self._lock_key}}}:candidates:{epoch}"

    def _winner_key(self, epoch: int) -> str:
        return f"{{{self._lock_key}}}:winner:{epoch}"

    async def _enroll(self, cand_key: str, instance_id: str) -> None:
        await self._redis.eval(
            _ENROLL_LUA,
            1,
            cand_key,
            instance_id,
            str(self._meta_ttl_sec),
        )

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

        await self._enroll(cand_key, iid)

        # 报名已耗时：用对表时钟（或再问一次 TIME）算剩余，避免按过期 now 睡过 T
        if planned_fire is not None:
            current = (
                self._clock.now()
                if self._clock is not None
                else await redis_unix_now(self._redis)
            )
            delay = fire_at - current
        else:
            delay = self._gather_window_sec
        if delay > 0:
            await asyncio.sleep(delay)

        # 抽签前再续一次 TTL，防止窗口偏大或抖动导致 candidates 已过期
        await self._enroll(cand_key, iid)

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
        # 常态降 DEBUG（1Hz 任务每拍一条会刷屏）；选主异常仍走 WARNING
        logger.debug(
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
