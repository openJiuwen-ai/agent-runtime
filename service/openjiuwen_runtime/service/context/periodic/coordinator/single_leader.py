# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""主备协调：提前报名 → 等到开火点 → 抽签选主 → 持锁续期执行。

流程（每拍，配合 JobRunner 提前 ``gather_window`` 醒来）：
1. ``planned_fire`` 为本拍整点 T；``now`` 多为 T-窗口
2. Lua ``SADD`` + ``EXPIRE`` 报名（epoch 取自 T，一次写完避免 key 泄漏）
3. 睡到 T（剩余窗口），让网络慢的实例也能进来
4. Lua 原子抽签：``SRANDMEMBER`` + ``SET NX winner:{epoch}``
5. 只有 winner 去 ``SET NX`` 执行锁，并启动续期；别人空转

EVAL 被禁（rename-command / ACL）的环境：报名/抽签经
:mod:`redis_compat` 惰性降级——报名为纯 MULTI（SADD+EXPIRE 一次
事务，防两步间崩溃留下无 TTL 报名键的性质保持）；抽签为
WATCH(winner/candidates 同槽)+复读+SET NX 事务，并发落定时
WatchError 重试后复读既有 winner，与 Lua 单线程串行化等价。
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any, Optional

from openjiuwen_runtime.foundation.log import get_logger
from redis.exceptions import WatchError

from ..clock import RedisAlignedClock, redis_unix_now
from ..lock import TickLock
from ...redis_compat import backoff_delay, eval_or_watch

logger = get_logger(__name__)

# 非空闭合 hash tag：{ 与 } 之间至少一个非花括号字符（{} 视为空 tag 无效）
_META_TAG_RE = re.compile(r"\{[^{}]+\}")

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


def _meta_stem(lock_key: str) -> str:
    """candidates/winner 派生键词干：保证两键同槽（Redis Cluster 双键 Lua）。

    - lock_key 自带非空闭合 hash tag（如 agent_runtime job 键
      ``<前缀>{agent_runtime:job:x}``，用户前缀在 tag 外）→ 整键作词干，
      前缀保留（多租户共享 Redis 时各部署选主互不串扰）；
    - 纯净无花括号的名字（如框架缺省 ``lock:<name>``，且不以 ``}`` 开头）
      → 整键包 brace，恢复 ``{lock_key}:candidates/{epoch}`` 形态
      （框架级同槽契约，任意调用方受益）；
    - 其余一律 raise ValueError（空串 / ``}`` 开头 / 花括号残缺如
      ``{}abc``、``x{}``、``foo{bar``——无法构成非空 tag，拒绝魔法修复）。
    执行锁为单键操作，无需与派生键同槽。
    """
    if _META_TAG_RE.search(lock_key):
        return lock_key
    if lock_key and "{" not in lock_key and not lock_key.startswith("}"):
        return "{%s}" % lock_key
    raise ValueError(
        f"invalid lock_key {lock_key!r}: 无法构造非空 hash tag "
        "(candidates/winner 双键 Lua 需同槽)，"
        "请使用形如 <前缀>{domain} 的键名或不含花括号的普通键名"
    )


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
        # 启动即校验 lock_key（病态键在 _meta_stem 内 raise，fail-fast）
        _meta_stem(lock_key)
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
        return f"{_meta_stem(self._lock_key)}:candidates:{epoch}"

    def _winner_key(self, epoch: int) -> str:
        return f"{_meta_stem(self._lock_key)}:winner:{epoch}"

    async def _enroll(self, cand_key: str, instance_id: str) -> None:
        await eval_or_watch(
            self._redis,
            lambda: self._redis.eval(
                _ENROLL_LUA,
                1,
                cand_key,
                instance_id,
                str(self._meta_ttl_sec),
            ),
            lambda: self._enroll_watch(cand_key, instance_id),
            op=f"enroll:{cand_key}",
        )

    async def _enroll_watch(self, cand_key: str, instance_id: str) -> int:
        """报名的 WATCH 版：纯 MULTI（SADD+EXPIRE 同事务，无需 WATCH——
        写集幂等且原子，防两步间崩溃留下无 TTL 报名键的性质保持）。"""
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.multi()
            pipe.sadd(cand_key, instance_id)
            pipe.expire(cand_key, self._meta_ttl_sec)
            resp = await pipe.execute()
        return int(resp[0] or 0) if resp else 0

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
        return await eval_or_watch(
            self._redis,
            lambda: self._redis.eval(
                _ELECT_LUA,
                2,
                winner_key,
                cand_key,
                str(self._meta_ttl_sec),
            ),
            lambda: self._elect_watch(winner_key, cand_key),
            op=f"elect:{winner_key}",
        )

    async def _elect_watch(self, winner_key: str, cand_key: str) -> Any:
        """抽签的 WATCH 版（winner/candidates 经 ``_meta_stem`` 同槽）。

        WATCH 两键 → 复读 winner（有则直接返回）→ SRANDMEMBER 抽签 →
        MULTI SET NX EX → EXEC。并发落定时 winner 变更触发 WatchError，
        重试后复读既有 winner——与 Lua「SET NX 失败则回读」语义等价；
        winner 自 WATCH 起未变 → NX 必成功，pick 即当选者。
        """
        for attempt in range(3):
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(winner_key, cand_key)
                    existing = await pipe.get(winner_key)
                    if existing is not None:
                        await pipe.unwatch()
                        return existing
                    pick = await pipe.srandmember(cand_key)
                    if pick is None:
                        await pipe.unwatch()
                        return None
                    pipe.multi()
                    pipe.set(winner_key, pick, nx=True, ex=self._meta_ttl_sec)
                    resp = await pipe.execute()
                    # SET NX 落定（防御：万一 NX 未落，回读写入者）
                    if resp and resp[0]:
                        return pick
                    return await self._redis.get(winner_key)
                except WatchError:
                    await asyncio.sleep(backoff_delay(attempt, base_ms=1.0, cap_ms=10.0))
                    continue
        # 重试耗尽（极端竞争）：退化为只读回现状，与本拍已有 winner 一致
        return await self._redis.get(winner_key)

    async def release(self, token: str) -> None:
        try:
            await self._lock.release_if_owner(token)
        except Exception:
            logger.exception(
                "single_leader release failed: key=%s token=%s",
                self._lock.lock_key,
                token,
            )

    def abandon_claim(self) -> None:
        """停掉续约，但不等待。

        卡死的 tick 被放弃时调用：续约任务若还在刷 TTL，新循环抢不到锁。
        停掉之后锁按剩余 TTL 过期（1Hz 任务约 1 秒）。
        """
        self._lock.stop_renew()
