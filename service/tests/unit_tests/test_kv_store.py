# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""KVStore 原语单测（设计 §9.3）：fakeredis。"""
import pytest
import fakeredis.aioredis

from openjiuwen_runtime.service.context.primitives.kv_store import KVStore


@pytest.mark.unit
async def test_set_get_roundtrip_and_missing():
    kv = KVStore(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await kv.set("mode", "agent.plan")
    assert await kv.get("mode") == "agent.plan"
    assert await kv.get("missing") is None


@pytest.mark.unit
async def test_exists_and_delete():
    kv = KVStore(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await kv.set("k", "v")
    assert await kv.exists("k") is True
    assert await kv.delete("k") is True
    assert await kv.exists("k") is False
    assert await kv.delete("k") is False        # 已删 → False


@pytest.mark.unit
async def test_incr_atomic_sequential():
    kv = KVStore(fakeredis.aioredis.FakeRedis(), prefix="svc")
    assert await kv.incr("counter") == 1
    assert await kv.incr("counter") == 2
    assert await kv.incr("counter") == 3
    assert await kv.incr("counter", amount=5) == 8


@pytest.mark.unit
async def test_incr_shared_across_two_instances():
    # 两个副本共享同一 redis（FakeServer）→ 计数全局递增（无内存状态硬约束）
    shared = fakeredis.aioredis.FakeServer()
    kv_a = KVStore(fakeredis.aioredis.FakeRedis(server=shared), prefix="svc")
    kv_b = KVStore(fakeredis.aioredis.FakeRedis(server=shared), prefix="svc")
    assert await kv_a.incr("echo:idx") == 1
    assert await kv_b.incr("echo:idx") == 2
    assert await kv_a.incr("echo:idx") == 3


@pytest.mark.unit
async def test_set_with_ttl_is_visible_and_expires():
    kv = KVStore(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await kv.set("tmp", "v", ttl=100)
    assert await kv.exists("tmp") is True
    # 底层 redis 已设置 TTL（白盒校验 EX 生效）
    assert await kv._redis.ttl(kv._key("tmp")) > 0


@pytest.mark.unit
async def test_json_get_set():
    kv = KVStore(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await kv.set_json("sess", {"mode": "plan", "n": 3}, ttl=100)
    assert await kv.get_json("sess") == {"mode": "plan", "n": 3}
    assert await kv.get_json("missing", default={}) == {}


@pytest.mark.unit
async def test_scan_returns_user_space_keys():
    kv = KVStore(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await kv.set("echo:a", "1")
    await kv.set("echo:b", "2")
    await kv.set("other:c", "3")
    found = sorted(await kv.scan("echo:*"))
    assert found == ["echo:a", "echo:b"]
