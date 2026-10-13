# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""redis_compat 原语与 EVAL 禁用降级路径的单测。

覆盖：
- ``eval_disabled_error`` 分类（rename-command / ACL 两族；非禁用错误不命中）
- ``watch_cas`` owner-token 校验 / token 不匹配 / 并发改写（WatchError）
- ``eval_or_watch`` 惰性降级 + 进程级 memo（后续调用免撞墙；非禁用错误上抛）
- ``RedisLockBackend`` 在 EVAL 禁用环境 renew/release 的 owner 校验仍成立
- ``backoff_delay`` 抖动边界
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from redis.exceptions import ConnectionError, ResponseError, WatchError

from openjiuwen_runtime.service.context.locks.backends.redis import RedisLockBackend
from openjiuwen_runtime.service.context.periodic.coordinator.single_leader import (
    SingleLeaderCoordinator,
)
from openjiuwen_runtime.service.context.periodic.lock import TickLock
from openjiuwen_runtime.service.context.redis_compat import (
    backoff_delay,
    eval_disabled_error,
    eval_or_watch,
    is_eval_disabled,
    mark_eval_disabled,
    watch_cas,
)


@pytest.mark.unit
def test_eval_disabled_error_classification():
    # rename-command EVAL "" 的真实报错形态（真环境 6399 实测）
    assert eval_disabled_error(
        ResponseError(
            "unknown command 'EVAL', with args beginning with: 'return 1' '1' 'x' "
        )
    )
    # ACL 撤权两族
    assert eval_disabled_error(
        ResponseError("NOPERM this user has no permissions to run the 'eval' command")
    )
    assert eval_disabled_error("user default has no permissions to run the eval command")
    # 非禁用错误不得命中（避免把网络/语法问题误判成禁用）
    assert not eval_disabled_error(ResponseError("ERR wrong number of arguments"))
    assert not eval_disabled_error(ConnectionError("Connection reset by peer"))
    assert not eval_disabled_error(ResponseError("unknown command 'FLUSHALL'"))
    # 大小写不敏感
    assert eval_disabled_error("Unknown command 'eval'")


@pytest.mark.unit
async def test_watch_cas_owner_match_mismatch_and_clobber():
    redis = fakeredis.aioredis.FakeRedis()
    await redis.set("lk", "tok-a")

    # owner 匹配 → 事务执行，响应取回
    resp = await watch_cas(redis, "lk", "tok-a", lambda p: p.delete("lk"))
    assert resp is not None and resp[0] == 1
    assert await redis.get("lk") is None

    # key 已不在 → GET 为 None ≠ token → 不动
    resp = await watch_cas(redis, "lk", "tok-a", lambda p: p.delete("lk"))
    assert resp is None

    # token 不匹配（他人持锁）→ 不删
    await redis.set("lk", "tok-b")
    resp = await watch_cas(redis, "lk", "tok-a", lambda p: p.delete("lk"))
    assert resp is None
    assert await redis.get("lk") == b"tok-b"

    # WATCH 后被并发改写 → WatchError → None，写集不落
    orig_pipeline = redis.pipeline

    class _RiggedPipeline:
        """包装真实 pipeline：MULTI 后、EXEC 前插入竞争写者。"""

        def __init__(self, inner):
            self._inner = inner
            self._armed = False

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            enter = getattr(self._inner, "__aenter__", None)
            if enter is not None:
                await enter()
            return self

        async def __aexit__(self, *exc):
            exit_ = getattr(self._inner, "__aexit__", None)
            if exit_ is not None:
                await exit_(*exc)

        def multi(self):
            self._armed = True
            self._inner.multi()

        async def execute(self, *a, **kw):
            if self._armed:
                # 经另一条连接直写同一 server：触发 WATCH 失效
                await redis.set("lk", "clobbered")
            return await self._inner.execute(*a, **kw)

    def rigged_pipeline(*a, **kw):
        return _RiggedPipeline(orig_pipeline(*a, **kw))

    redis.pipeline = rigged_pipeline  # type: ignore[method-assign]
    try:
        resp = await watch_cas(redis, "lk", "tok-b", lambda p: p.delete("lk"))
    finally:
        redis.pipeline = orig_pipeline  # type: ignore[method-assign]
    assert resp is None
    assert await redis.get("lk") == b"clobbered"


@pytest.mark.unit
async def test_eval_or_watch_lazy_fallback_and_memo():
    redis = fakeredis.aioredis.FakeRedis()

    calls = {"eval": 0, "watch": 0}

    async def eval_call():
        calls["eval"] += 1
        raise ResponseError("unknown command 'EVAL', with args beginning with:")

    async def watch_call():
        calls["watch"] += 1
        return "watched"

    # 首次：撞 eval → 降级 watch + memo
    assert await eval_or_watch(redis, eval_call, watch_call, op="t") == "watched"
    assert calls == {"eval": 1, "watch": 1}
    assert is_eval_disabled(redis)

    # 后续：memo 命中，直接 watch（不再撞 eval）
    assert await eval_or_watch(redis, eval_call, watch_call, op="t") == "watched"
    assert calls == {"eval": 1, "watch": 2}

    # memo 可清除（测试隔离用）
    mark_eval_disabled(redis, False)
    assert not is_eval_disabled(redis)

    # 非禁用错误：原样上抛，不降级不 memo
    async def conn_error():
        raise ConnectionError("boom")

    with pytest.raises(ConnectionError):
        await eval_or_watch(redis, conn_error, watch_call, op="t")
    assert not is_eval_disabled(redis)


@pytest.mark.unit
async def test_redis_lock_backend_works_when_eval_disabled():
    redis = fakeredis.aioredis.FakeRedis()
    backend = RedisLockBackend(redis, prefix="test", instance_id="i1")

    # 模拟服务端禁 EVAL：client.eval 一律拒绝
    orig_eval = redis.eval

    async def refused_eval(*a, **kw):
        raise ResponseError("unknown command 'EVAL', with args beginning with:")

    redis.eval = refused_eval  # type: ignore[method-assign]
    try:
        cred = await backend.try_acquire("job", ttl=30)
        assert cred is not None

        # renew 走 WATCH 降级仍成功
        renewed = await backend.renew(cred, ttl=30)
        assert renewed.expires_at > cred.expires_at

        # 他人夺锁后 renew/release 失败（owner 校验仍在）
        await redis.set(cred.key, "other-owner")
        with pytest.raises(Exception):
            await backend.renew(cred, ttl=30)
        assert await backend.release(cred) is False

        # 回到自主持有：release 成功
        await redis.set(cred.key, cred.token.encode())
        assert await backend.release(cred) is True
    finally:
        redis.eval = orig_eval  # type: ignore[method-assign]


@pytest.mark.unit
def test_backoff_delay_bounds():
    for attempt in range(10):
        d = backoff_delay(attempt)
        assert 0.0 <= d < 0.05  # 默认 cap 50ms
    # 上限生效（attempt 很大也不超过 cap）
    assert backoff_delay(30) < 0.05
    assert backoff_delay(0, base_ms=2.0, cap_ms=10.0) < 0.01


def _refuse_eval(redis):
    """给 client 装上「服务端禁 EVAL」的拒绝桩，返回还原函数。"""
    orig = redis.eval

    async def refused(*a, **kw):
        raise ResponseError("unknown command 'EVAL', with args beginning with:")

    redis.eval = refused  # type: ignore[method-assign]
    return orig


@pytest.mark.unit
async def test_tick_lock_renew_release_when_eval_disabled():
    redis = fakeredis.aioredis.FakeRedis()
    orig = _refuse_eval(redis)
    try:
        lock = TickLock(
            redis, lock_key="{job}:test", lock_ttl_sec=2,
            token_prefix="job", instance_id="i1",
        )
        tok = await lock.try_acquire()
        assert tok is not None
        # 续期走 WATCH 降级：owner 命中
        assert await lock.renew_once(tok) is True
        # 非 owner token：不动
        assert await lock.renew_once("job:i1:not-mine") is False
        assert await redis.ttl("{job}:test") > 0
        # 释放：owner 命中；再释放幂等 False
        assert await lock.release_if_owner(tok) is True
        assert await lock.release_if_owner(tok) is False
        assert await redis.get("{job}:test") is None
    finally:
        redis.eval = orig  # type: ignore[method-assign]


@pytest.mark.unit
async def test_single_leader_elects_exactly_one_when_eval_disabled():
    redis = fakeredis.aioredis.FakeRedis()
    orig = _refuse_eval(redis)
    try:
        coord_a = SingleLeaderCoordinator(
            redis, lock_key="{agent_runtime:job:test}",
            instance_id="a", gather_window_sec=0.0,
        )
        coord_b = SingleLeaderCoordinator(
            redis, lock_key="{agent_runtime:job:test}",
            instance_id="b", gather_window_sec=0.0,
        )
        now = 1000.0
        tok_a = await coord_a.try_claim(now=now, instance_id="a", planned_fire=now)
        tok_b = await coord_b.try_claim(now=now, instance_id="b", planned_fire=now)
        # 恰一个当选（报名→抽签→执行锁 SET NX 全链无 Lua）
        assert (tok_a is None) != (tok_b is None)
        winner = coord_a if tok_a else coord_b
        loser = coord_b if tok_a else coord_a
        token = tok_a or tok_b
        await winner.release(token)
        # 释放后下一拍落选者可当选（epoch 前进避免元数据串拍）
        tok_b2 = await loser.try_claim(
            now=now + 5, instance_id="b", planned_fire=now + 5
        )
        assert tok_b2 is not None
        await loser.release(tok_b2)
    finally:
        redis.eval = orig  # type: ignore[method-assign]


@pytest.mark.unit
async def test_single_leader_elect_persists_existing_winner():
    """已有 winner 时后到的抽签直接回读既有当选者（不覆盖）。"""
    redis = fakeredis.aioredis.FakeRedis()
    coord = SingleLeaderCoordinator(
        redis, lock_key="{agent_runtime:job:test2}",
        instance_id="a", gather_window_sec=0.0,
    )
    await redis.set("{agent_runtime:job:test2}:winner:7", "someone-else", ex=10)
    winner = await coord._elect(
        "{agent_runtime:job:test2}:winner:7",
        "{agent_runtime:job:test2}:candidates:7",
    )
    assert winner == b"someone-else"
