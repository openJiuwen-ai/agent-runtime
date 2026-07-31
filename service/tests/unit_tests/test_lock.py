# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""DistributedLock 原语单测（设计 §9.1）：SET NX EX + WATCH CAS 释放 + 续期/失锁。"""
import asyncio
import time

import fakeredis.aioredis
import pytest

from openjiuwen_runtime.service.context.primitives.lock import DistributedLock
from openjiuwen_runtime.service.errors import LockLost, LockNotAcquired


def _new(redis=None, **kw):
    kw.setdefault("owner", "A")
    kw.setdefault("ttl", 10)
    return DistributedLock(redis or fakeredis.aioredis.FakeRedis(), "res", **kw)


@pytest.mark.unit
async def test_acquire_then_release_allows_reacquire():
    redis = fakeredis.aioredis.FakeRedis()
    async with _new(redis):
        pass
    # 释放后另一个 owner 可立即获取
    async with DistributedLock(redis, "res", owner="B", ttl=10):
        pass


@pytest.mark.unit
async def test_nonblocking_conflict_raises_lock_not_acquired():
    redis = fakeredis.aioredis.FakeRedis()
    async with _new(redis, owner="A"):
        with pytest.raises(LockNotAcquired):
            async with DistributedLock(redis, "res", owner="B", ttl=10, timeout=0):
                pass


@pytest.mark.unit
async def test_blocking_timeout_eventually_raises():
    redis = fakeredis.aioredis.FakeRedis()
    async with _new(redis, owner="A"):
        t0 = time.monotonic()
        with pytest.raises(LockNotAcquired):
            async with DistributedLock(redis, "res", owner="B", ttl=10, timeout=0.4):
                pass
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.3            # 至少等了一段（阻塞）


@pytest.mark.unit
async def test_safe_release_does_not_delete_others_lock():
    redis = fakeredis.aioredis.FakeRedis()
    lk = _new(redis, owner="A")
    async with lk:
        # 模拟：A 持有期间锁过期并被 B 抢走（覆写 value）
        await redis.set(lk.key, "B")
    # A 退出不应删除 B 的锁（CAS：value 不匹配）
    assert await redis.get(lk.key) == b"B"


@pytest.mark.unit
async def test_renew_once_true_when_owner_false_when_lost():
    redis = fakeredis.aioredis.FakeRedis()
    lk = _new(redis, owner="A")
    async with lk:
        assert await lk.renew_once() is True       # 仍是 owner → 续期成功
        await redis.delete(lk.key)                  # 模拟失锁
        assert await lk.renew_once() is False       # 失锁 → 续期失败


@pytest.mark.unit
async def test_lock_lost_surfaces_on_exit():
    redis = fakeredis.aioredis.FakeRedis()
    lk = _new(redis, owner="A", ttl=10, renew_interval=0.05)
    with pytest.raises(LockLost):
        async with lk:
            await redis.delete(lk.key)             # 续期循环将检测到失锁
            await asyncio.sleep(0.2)               # 等续期循环跑一轮
