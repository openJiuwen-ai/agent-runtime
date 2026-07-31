# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SystemContext / RequestContext 单测（设计 §8）：进程级/请求级、start/stop、for_request、transaction。"""
import logging

import fakeredis.aioredis
import pytest

from openjiuwen_runtime.service.context.system_context import SystemContext
from openjiuwen_runtime.service.envelope import Metadata


@pytest.mark.unit
async def test_start_stop_and_kv_on_request_context():
    ctx = SystemContext(redis=fakeredis.aioredis.FakeRedis())
    await ctx.start()
    try:
        rctx = ctx.for_request(Metadata(request_id="r1"))
        # 请求上下文上的 kv 绑定进程级 redis → 原子递增
        assert await rctx.kv.incr("c") == 1
        assert await rctx.kv.incr("c") == 2
    finally:
        await ctx.stop()


@pytest.mark.unit
async def test_for_request_propagates_metadata_and_lock_owner():
    ctx = SystemContext(redis=fakeredis.aioredis.FakeRedis())
    await ctx.start()
    try:
        rctx = ctx.for_request(Metadata(request_id="r1", user_id="u", session_id="s", trace_id="t"))
        assert rctx.request_id == "r1"
        assert rctx.user_id == "u"
        assert rctx.session_id == "s"
        assert rctx.trace_id == "t"
        assert isinstance(rctx.logger, logging.Logger)
        assert ctx.instance_id in rctx.lock_owner        # lock_owner 含 instance_id
    finally:
        await ctx.stop()


@pytest.mark.unit
async def test_lock_owner_unique_per_request():
    ctx = SystemContext(redis=fakeredis.aioredis.FakeRedis())
    a = ctx.for_request(Metadata(request_id="r1"))
    b = ctx.for_request(Metadata(request_id="r2"))
    assert a.lock_owner != b.lock_owner


@pytest.mark.unit
async def test_start_and_stop_are_idempotent():
    ctx = SystemContext(redis=fakeredis.aioredis.FakeRedis())
    await ctx.start()
    await ctx.start()        # 幂等，不报错
    await ctx.stop()
    await ctx.stop()         # 幂等，不报错


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True


class _FakeDb:
    """模拟 SQLAlchemyHandler：``session_factory()`` 返回一个 session。"""

    def __init__(self) -> None:
        self.created: list[_FakeSession] = []

    def session_factory(self) -> _FakeSession:
        s = _FakeSession()
        self.created.append(s)
        return s


@pytest.mark.unit
async def test_transaction_commits_on_success_and_closes():
    ctx = SystemContext(db=_FakeDb())
    async with ctx.transaction() as s:
        assert isinstance(s, _FakeSession)
    assert s.committed is True
    assert s.closed is True
    assert s.rolled_back is False


@pytest.mark.unit
async def test_transaction_rolls_back_on_exception():
    ctx = SystemContext(db=_FakeDb())
    with pytest.raises(ValueError):
        async with ctx.transaction() as s:
            raise ValueError("boom")
    assert s.rolled_back is True
    assert s.closed is True
    assert s.committed is False


@pytest.mark.unit
async def test_transaction_without_db_raises():
    ctx = SystemContext()
    with pytest.raises(Exception):
        async with ctx.transaction() as _:
            pass


@pytest.mark.unit
def test_from_settings_builds_redis_from_env(monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_SERVICE_REDIS_URL", "redis://localhost:6379/0")
    ctx = SystemContext.from_settings()
    assert ctx.redis is not None
    assert ctx._owns_redis is True        # 自建 → stop 时负责关闭
