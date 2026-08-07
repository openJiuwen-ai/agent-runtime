# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Shared cache contract and request integration tests."""

from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest
from pydantic import BaseModel

from openjiuwen_runtime.service import (
    Cache,
    CacheUnavailable,
    MemoryCacheBackend,
    RedisCacheBackend,
    build_cache_backend,
)
from openjiuwen_runtime.service.context.system_context import SystemContext
from openjiuwen_runtime.service.envelope import Metadata


def _backend(kind: str, **kwargs):
    if kind == "memory":
        return MemoryCacheBackend(prefix="test", **kwargs)
    return RedisCacheBackend(
        fakeredis.aioredis.FakeRedis(),
        prefix="test",
        **kwargs,
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["memory", "redis"])
async def test_cache_backends_share_crud_contract(kind):
    backend = _backend(kind, default_ttl=60)

    assert await backend.get("missing") is None
    await backend.set("key", "value")
    assert await backend.get("key") == "value"
    assert await backend.exists("key") is True
    assert await backend.delete("key") is True
    assert await backend.delete("key") is False
    assert await backend.exists("key") is False
    assert backend.metrics.hits == 1
    assert backend.metrics.misses == 1

    await backend.close()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["memory", "redis"])
async def test_cache_backends_expire_values(kind):
    backend = _backend(kind, default_ttl=None)
    await backend.set("temporary", "value", ttl=0.02)
    assert await backend.get("temporary") == "value"

    await asyncio.sleep(0.04)

    assert await backend.get("temporary") is None
    await backend.close()


@pytest.mark.unit
async def test_memory_cache_uses_lru_capacity_eviction():
    backend = MemoryCacheBackend(
        prefix="test",
        default_ttl=None,
        max_entries=2,
    )
    await backend.set("first", "1")
    await backend.set("second", "2")
    assert await backend.get("first") == "1"

    await backend.set("third", "3")

    assert await backend.get("first") == "1"
    assert await backend.get("second") is None
    assert await backend.get("third") == "3"
    assert backend.metrics.evictions == 1


@pytest.mark.unit
async def test_memory_cache_lazily_removes_expired_entries():
    backend = MemoryCacheBackend(
        prefix="test",
        default_ttl=None,
        max_entries=2,
    )
    await backend.set("expired", "1", ttl=0.01)
    await asyncio.sleep(0.02)

    await backend.set("active", "2")

    assert backend.metrics.expirations == 1
    assert backend.metrics.evictions == 0
    assert await backend.exists("expired") is False


@pytest.mark.unit
async def test_memory_cache_concurrent_writes_remain_bounded():
    backend = MemoryCacheBackend(
        prefix="test",
        default_ttl=None,
        max_entries=10,
    )

    await asyncio.gather(*(backend.set(str(index), str(index)) for index in range(100)))

    assert await backend.get("0") is None
    assert backend.metrics.evictions == 90
    assert await backend.get("99") == "99"


class _User(BaseModel):
    user_id: int
    name: str


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["memory", "redis"])
async def test_cache_json_and_pydantic_roundtrip(kind):
    backend = _backend(kind, default_ttl=60)
    client = Cache(backend)
    user = _User(user_id=7, name="Ada")

    await client.set_json("user:7", user)

    assert await client.get_json("user:7") == {"user_id": 7, "name": "Ada"}
    assert await client.get_model("user:7", _User) == user
    assert await client.get_json("missing", default={}) == {}

    await backend.set_json("user:8", _User(user_id=8, name="Lin"))
    assert await backend.get_model("user:8", _User) == _User(user_id=8, name="Lin")


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["memory", "redis"])
async def test_cache_rejects_values_over_utf8_size_limit(kind):
    backend = _backend(kind, default_ttl=60, max_value_bytes=3)

    with pytest.raises(ValueError, match="limit is 3 bytes"):
        await backend.set("key", "中文")


@pytest.mark.unit
async def test_redis_cache_is_shared_and_clear_is_namespaced():
    server = fakeredis.aioredis.FakeServer()
    redis_a = fakeredis.aioredis.FakeRedis(server=server)
    redis_b = fakeredis.aioredis.FakeRedis(server=server)
    users = RedisCacheBackend(redis_a, prefix="users", default_ttl=None)
    sessions = RedisCacheBackend(redis_b, prefix="sessions", default_ttl=None)
    users_replica = RedisCacheBackend(redis_b, prefix="users", default_ttl=None)
    await asyncio.gather(*(users.set(str(index), str(index)) for index in range(150)))
    await sessions.set("7", "active")

    assert await users_replica.get("7") == "7"
    assert await users.clear_namespace() == 150
    assert await users_replica.get("7") is None
    assert await sessions.get("7") == "active"


class _BrokenRedis:
    async def get(self, key):
        raise ConnectionError("offline")


@pytest.mark.unit
async def test_redis_cache_normalizes_backend_errors_and_counts_them():
    backend = RedisCacheBackend(_BrokenRedis(), prefix="test")

    with pytest.raises(CacheUnavailable, match="cache get failed"):
        await backend.get("key")

    assert backend.metrics.backend_errors == 1


@pytest.mark.unit
async def test_request_cache_cleanup_closes_only_request_facade():
    backend = MemoryCacheBackend(prefix="test", default_ttl=None)
    sysctx = SystemContext(cache_backend=backend)
    first = sysctx.for_request(Metadata(request_id="r1"))
    client = first.cache
    await client.set_json("key", {"value": 1})

    await first.close()

    with pytest.raises(CacheUnavailable, match="request cache client is closed"):
        await client.get("key")
    second = sysctx.for_request(Metadata(request_id="r2"))
    assert await second.cache.get_json("key") == {"value": 1}
    await second.close()


@pytest.mark.unit
async def test_system_context_requires_and_closes_cache_backend():
    missing = SystemContext().for_request(Metadata(request_id="missing"))
    with pytest.raises(CacheUnavailable, match="cache backend is not configured"):
        _ = missing.cache

    backend = MemoryCacheBackend(prefix="test")
    sysctx = SystemContext(cache_backend=backend)
    await sysctx.start()
    await sysctx.stop()
    with pytest.raises(CacheUnavailable, match="cache backend is closed"):
        await backend.get("key")


@pytest.mark.unit
def test_cache_backend_factory_selects_memory_redis_and_none():
    redis = fakeredis.aioredis.FakeRedis()

    assert isinstance(build_cache_backend("memory"), MemoryCacheBackend)
    assert isinstance(build_cache_backend("redis", redis=redis), RedisCacheBackend)
    assert build_cache_backend("none") is None
    with pytest.raises(CacheUnavailable):
        build_cache_backend("redis")
    with pytest.raises(ValueError):
        build_cache_backend("unknown")
