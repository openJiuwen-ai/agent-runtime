# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""幂等原语 + idempotency_guard 中间件单测（设计 §9.2）。"""
import fakeredis.aioredis
import pytest

from openjiuwen_runtime.service.context.primitives.idempotency import Idempotency, idempotency_guard
from openjiuwen_runtime.service.envelope import Envelope, Metadata, ResponseEnvelope
from openjiuwen_runtime.service.context.system_context import SystemContext
from openjiuwen_runtime.service.routing.router import MessageRouter


@pytest.mark.unit
async def test_acquire_first_then_duplicate():
    idem = Idempotency(fakeredis.aioredis.FakeRedis(), prefix="svc")
    g1 = await idem.acquire("r1", window=60)
    assert g1.acquired is True
    assert g1.cached_result is None
    g2 = await idem.acquire("r1", window=60)
    assert g2.acquired is False            # 同 request_id 重复 → 不再获得


@pytest.mark.unit
async def test_cache_mode_replays_first_result():
    idem = Idempotency(fakeredis.aioredis.FakeRedis(), prefix="svc")
    g1 = await idem.acquire("r1", window=60)
    assert g1.acquired
    await g1.succeed(ResponseEnvelope(type="echo", metadata=Metadata(request_id="r1"),
                                      rawdata={"v": 9}, ok=True))
    g2 = await idem.acquire("r1", window=60)
    assert g2.acquired is False
    assert g2.cached_result is not None
    assert g2.cached_result.rawdata == {"v": 9}


def _env(rid):
    return Envelope(type="ping", metadata=Metadata(request_id=rid), rawdata={})


@pytest.mark.unit
async def test_guard_reject_mode_runs_handler_once():
    ctx = SystemContext(redis=fakeredis.aioredis.FakeRedis())
    await ctx.start()
    rctx = ctx.for_request(Metadata(request_id="r1"))
    router = MessageRouter()
    router.use(idempotency_guard(window=60, mode="reject"))
    calls = []

    @router.handle("ping")
    async def ping(c, env):
        calls.append(1)
        return {"pong": await c.kv.incr("p")}

    res1 = await router.dispatch(_env("r1"), rctx)
    res2 = await router.dispatch(_env("r1"), rctx)
    assert res1.response.ok and res1.response.rawdata == {"pong": 1}
    assert res2.response.ok is False
    assert res2.response.error_code == "idempotent"      # 重复 → 拒绝
    assert len(calls) == 1                                # handler 只跑一次
    await ctx.stop()


@pytest.mark.unit
async def test_guard_cache_mode_replays_result_without_rerunning():
    ctx = SystemContext(redis=fakeredis.aioredis.FakeRedis())
    await ctx.start()
    rctx = ctx.for_request(Metadata(request_id="r1"))
    router = MessageRouter()
    router.use(idempotency_guard(window=60, mode="cache"))
    calls = []

    @router.handle("ping")
    async def ping(c, env):
        calls.append(1)
        return {"pong": await c.kv.incr("p")}

    res1 = await router.dispatch(_env("r1"), rctx)
    res2 = await router.dispatch(_env("r1"), rctx)
    assert res1.response.ok and res1.response.rawdata == {"pong": 1}
    assert res2.response.ok is True
    assert res2.response.rawdata == {"pong": 1}           # 回放首次结果
    assert len(calls) == 1                                # handler 只跑一次
    await ctx.stop()
