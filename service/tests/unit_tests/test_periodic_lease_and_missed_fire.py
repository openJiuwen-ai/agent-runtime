# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""周期任务的租约与开火判据回归（2026-11 现网事故）。

事故形态（公用高延迟 Redis + 无 Lua 模式）：
- 执行锁 TTL 恒等于调度间隔 → 1s 间隔任务拿 1s 租约、0.33s 续一次，一次续期
  往返超过 1s 即失锁，正在跑的那一拍被 abort（日志：renew_not_owner +
  on_tick aborted，而该拍本身允许跑 370s）；
- 醒来后那次 Redis ``TIME`` 对表的往返耗时被算进「睡过头」判据 → RTT 超过
  集合窗口（80ms）时 1Hz 任务每拍必被判 missed fire 整拍丢弃。
"""
import asyncio

import fakeredis.aioredis
import pytest

from openjiuwen_runtime.service.context.periodic.factory import (
    _LOCK_TTL_FLOOR_SEC,
    create_single_leader_job,
)
from openjiuwen_runtime.service.context.periodic.lock import TickLock
from openjiuwen_runtime.service.context.periodic.runner import JobRunner
from openjiuwen_runtime.service.context.periodic.schedule.interval import (
    IntervalSchedule,
)


class _Ctx:
    """SystemContext 的最小替身（factory 只用 require_redis / instance_id）。"""

    def __init__(self, redis):
        self._redis = redis
        self.instance_id = "inst-1"

    def require_redis(self):
        return self._redis


def _runner(interval: int = 1) -> JobRunner:
    redis = fakeredis.aioredis.FakeRedis()

    async def _noop() -> None:
        return None

    return JobRunner(
        name="j",
        schedule=IntervalSchedule(interval),
        coordinator=object(),      # _missed_fire 不碰协调器
        on_tick=_noop,
        instance_id="inst-1",
        redis=redis,
    )


# ---------------------------------------------------------------- 租约下限


@pytest.mark.unit
def test_lock_ttl_floors_above_schedule_interval():
    """1s 间隔任务不再拿 1s 租约（续期赶不上过期 → 跑着的一拍被 abort）。"""
    job = create_single_leader_job(
        _Ctx(fakeredis.aioredis.FakeRedis()),
        name="j1",
        on_tick=lambda: asyncio.sleep(0),
        interval_sec=1,
        tick_timeout_sec=370,
    )
    assert job._coordinator._lock._lock_ttl_sec == _LOCK_TTL_FLOOR_SEC
    # 续期节拍仍是 TTL/3，于是从 0.33s 放宽到 3.3s——够盖住无 Lua 的多往返 CAS
    assert job._coordinator._lock._renew_interval_sec == pytest.approx(
        _LOCK_TTL_FLOOR_SEC / 3.0)


@pytest.mark.unit
def test_lock_ttl_follows_long_interval_and_explicit_override():
    """间隔大于下限时仍跟间隔；显式入参优先（调用方比框架更懂自己的拍长）。"""
    ctx = _Ctx(fakeredis.aioredis.FakeRedis())
    long_job = create_single_leader_job(
        ctx, name="j2", on_tick=lambda: asyncio.sleep(0), interval_sec=30)
    assert long_job._coordinator._lock._lock_ttl_sec == 30
    pinned = create_single_leader_job(
        ctx, name="j3", on_tick=lambda: asyncio.sleep(0),
        interval_sec=1, lock_ttl_sec=380)
    assert pinned._coordinator._lock._lock_ttl_sec == 380


# ---------------------------------------------------------------- 续期韧性


@pytest.mark.unit
async def test_renew_retries_once_before_declaring_lock_lost():
    """单次续期抖动不判失锁（重试一次成功 → 本拍继续）。"""
    redis = fakeredis.aioredis.FakeRedis()
    lock = TickLock(redis, lock_key="k", lock_ttl_sec=2,
                    token_prefix="j", instance_id="inst-1",
                    renew_interval_sec=0.01)
    token = await lock.try_acquire()
    assert token is not None

    calls = {"n": 0}
    real = lock.renew_once

    async def flaky(tok: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("redis hiccup")
        return await real(tok)

    lock.renew_once = flaky          # type: ignore[assignment]
    lock.start_renew(token)
    await asyncio.sleep(0.08)
    assert calls["n"] >= 2
    assert not lock.lost              # 抖动后重试成功 → 仍持有
    lock.stop_renew()


@pytest.mark.unit
async def test_renew_marks_lost_after_consecutive_failures():
    """连续失败仍判失锁（续期挂了不能让业务以为还握着锁）。"""
    redis = fakeredis.aioredis.FakeRedis()
    lock = TickLock(redis, lock_key="k", lock_ttl_sec=2,
                    token_prefix="j", instance_id="inst-1",
                    renew_interval_sec=0.01)
    token = await lock.try_acquire()

    async def always_fail(tok: str) -> bool:
        raise ConnectionError("redis down")

    lock.renew_once = always_fail     # type: ignore[assignment]
    lock.start_renew(token)
    await asyncio.wait_for(lock.lost_event.wait(), timeout=2)
    assert lock.lost


# ---------------------------------------------------------------- 开火判据


@pytest.mark.unit
def test_clock_sync_cost_is_not_oversleep():
    """醒来后的对表往返不算睡过头：RTT 0.3s > 集合窗口 0.08s 仍照常开火。"""
    runner = _runner(interval=1)
    planned = 1000.0
    wake_at = planned - 0.08          # 集合窗口内提前醒
    # 对表返回的时刻已越过开火点，但其中 0.3s 全花在这次 TIME 往返上
    assert runner._missed_fire(
        planned + 0.3, planned_fire=planned, wake_at=wake_at, slack=0.3) is False
    # 不扣除就会被误判（改前的行为，回归锚）
    assert runner._missed_fire(
        planned + 0.3, planned_fire=planned, wake_at=wake_at, slack=0.0) is True


@pytest.mark.unit
def test_real_oversleep_still_skips_tick():
    """真的睡过头照旧跳拍：扣掉对表耗时后仍进入下一拍。"""
    runner = _runner(interval=1)
    planned = 1000.0
    assert runner._missed_fire(
        planned + 1.5, planned_fire=planned, wake_at=planned - 0.08,
        slack=0.1) is True
