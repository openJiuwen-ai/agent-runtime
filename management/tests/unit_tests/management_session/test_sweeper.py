# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Sweeper 单元测试（P0+P1）。"""

from __future__ import annotations

import asyncio
import math
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openjiuwen_runtime.management.session.sweeper import (
    ExpiryStore,
    SweepLock,
    Sweeper,
    SweeperConfig,
    SweeperRunner,
    sleep_until_next_boundary,
)
from openjiuwen_runtime.management.session.sweeper import keys as sk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_redis import FakeAsyncRedis  # noqa: E402


async def seed_session(
    redis: FakeAsyncRedis,
    *,
    session_id: str,
    service_id: str,
    endpoint_id: str,
    expiry: float,
) -> None:
    await redis.hset(
        sk.session_key(session_id),
        mapping={
            "service_id": service_id,
            "endpoint_id": endpoint_id,
            "expiry": str(int(expiry)),
            "session_ttl": "60",
        },
    )
    await redis.sadd(sk.scope_sessions_key(service_id), session_id)
    await redis.sadd(sk.pod_sessions_key(service_id, endpoint_id), session_id)
    await redis.zadd(sk.SESSION_EXPIRY, {session_id: float(expiry)})


@pytest.mark.asyncio
async def test_sweep_once_evicts_expired_four_places_and_publishes() -> None:
    redis = FakeAsyncRedis()
    now = 1_700_000_100
    await seed_session(
        redis,
        session_id="sess_1",
        service_id="svc_A",
        endpoint_id="pod_1",
        expiry=now - 10,
    )
    # 同 Pod 另有未到期会话 → 本轮不触发 idle_consider
    await seed_session(
        redis,
        session_id="sess_live",
        service_id="svc_A",
        endpoint_id="pod_1",
        expiry=now + 60,
    )

    resource = AsyncMock()
    sweeper = Sweeper(ExpiryStore(redis), resource)
    stats = await sweeper.sweep_once(now=now)

    assert stats.expired_count == 1
    assert stats.evict_ok == 1
    assert await redis.hgetall(sk.session_key("sess_1")) == {}
    assert "sess_1" not in await redis.smembers(sk.scope_sessions_key("svc_A"))
    assert "sess_1" not in await redis.smembers(sk.pod_sessions_key("svc_A", "pod_1"))
    assert "sess_1" not in await redis.zrangebyscore(sk.SESSION_EXPIRY, "-inf", "+inf")
    assert ("scope:svc_A:free", "1") in redis.published
    assert "sess_live" in await redis.smembers(sk.scope_sessions_key("svc_A"))
    resource.idle_consider.assert_not_called()


@pytest.mark.asyncio
async def test_pass_b_idle_consider_when_pod_emptied() -> None:
    redis = FakeAsyncRedis()
    now = 1_700_000_200
    await seed_session(
        redis,
        session_id="sess_only",
        service_id="svc_A",
        endpoint_id="pod_1",
        expiry=now - 1,
    )
    resource = AsyncMock()
    sweeper = Sweeper(ExpiryStore(redis, idle_notify_ttl_sec=60), resource)

    stats = await sweeper.sweep_once(now=now)
    assert stats.idle_consider_count == 1
    resource.idle_consider.assert_awaited_once_with("pod_1", service_id="svc_A")
    assert await redis.get(sk.pod_idle_notified_key("svc_A", "pod_1")) == "1"


@pytest.mark.asyncio
async def test_idle_notified_dedupes_resource_calls() -> None:
    redis = FakeAsyncRedis()
    store = ExpiryStore(redis, idle_notify_ttl_sec=60)
    assert await store.try_mark_idle_notified("svc_A", "pod_1") is True
    assert await store.try_mark_idle_notified("svc_A", "pod_1") is False


@pytest.mark.asyncio
async def test_dual_instance_only_one_acquires_lock() -> None:
    redis = FakeAsyncRedis()
    lock_a = SweepLock(
        redis, lock_key="lock:sweep", lock_ttl_sec=1, token_prefix="sweeper", instance_id="a"
    )
    lock_b = SweepLock(
        redis, lock_key="lock:sweep", lock_ttl_sec=1, token_prefix="sweeper", instance_id="b"
    )
    tok_a, tok_b = await asyncio.gather(lock_a.try_acquire(), lock_b.try_acquire())
    winners = [t for t in (tok_a, tok_b) if t is not None]
    assert len(winners) == 1
    await (lock_a if tok_a else lock_b).release_if_owner(winners[0])
    tok2 = await (lock_b if tok_a else lock_a).try_acquire()
    assert tok2 is not None


@pytest.mark.asyncio
async def test_runner_single_tick_sweeps_when_lock_held() -> None:
    redis = FakeAsyncRedis()
    now = 1_700_000_300
    await seed_session(
        redis,
        session_id="sess_x",
        service_id="svc_A",
        endpoint_id="pod_1",
        expiry=now - 1,
    )
    cfg = SweeperConfig(interval_sec=1, lock_ttl_sec=1)
    t = {"v": float(now)}
    first_sleep_done = asyncio.Event()
    block = asyncio.Event()
    sleeps = {"n": 0}

    async def sleep_fn(delay: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            first_sleep_done.set()
            return
        await block.wait()

    resource = AsyncMock()
    runner = SweeperRunner(
        cfg,
        redis,
        instance_id="solo",
        resource_client=resource,
        time_fn=lambda: t["v"],
        sleep_fn=sleep_fn,
    )
    await runner.start()
    await asyncio.wait_for(first_sleep_done.wait(), timeout=1.0)
    await asyncio.sleep(0.05)
    assert await redis.hgetall(sk.session_key("sess_x")) == {}
    assert await redis.get(cfg.lock_key) is None
    resource.idle_consider.assert_awaited_once_with("pod_1", service_id="svc_A")
    block.set()
    await runner.stop()


@pytest.mark.asyncio
async def test_sleep_until_next_boundary_aligns() -> None:
    slept: list[float] = []

    async def sleep_fn(delay: float) -> None:
        slept.append(delay)

    await sleep_until_next_boundary(1, time_fn=lambda: 10.2, sleep_fn=sleep_fn)
    assert len(slept) == 1
    assert math.isclose(slept[0], 0.8, rel_tol=0, abs_tol=1e-9)


@pytest.mark.asyncio
async def test_release_skips_if_not_owner() -> None:
    redis = FakeAsyncRedis()
    lock_a = SweepLock(
        redis, lock_key="lock:sweep", lock_ttl_sec=1, token_prefix="sweeper", instance_id="a"
    )
    lock_b = SweepLock(
        redis, lock_key="lock:sweep", lock_ttl_sec=1, token_prefix="sweeper", instance_id="b"
    )
    tok_a = await lock_a.try_acquire()
    assert tok_a
    redis.kv.clear()
    tok_b = await lock_b.try_acquire()
    assert tok_b
    assert await lock_a.release_if_owner(tok_a) is False
    assert await redis.get("lock:sweep") == tok_b
    assert await lock_b.release_if_owner(tok_b) is True
    assert await redis.get("lock:sweep") is None


@pytest.mark.asyncio
async def test_runner_stop_exits_cleanly() -> None:
    redis = FakeAsyncRedis()
    cfg = SweeperConfig(interval_sec=1)
    gate = asyncio.Event()

    async def sleep_fn(delay: float) -> None:
        await gate.wait()

    runner = SweeperRunner(
        cfg,
        redis,
        instance_id="x",
        time_fn=time.time,
        sleep_fn=sleep_fn,
    )
    await runner.start()
    assert runner._task is not None and not runner._task.done()  # noqa: SLF001
    stop_task = asyncio.create_task(runner.stop())
    await asyncio.sleep(0.01)
    gate.set()
    await stop_task
    assert runner._task is None  # noqa: SLF001
