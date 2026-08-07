# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""统一锁后端契约测试。"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import fakeredis.aioredis
import pytest

from openjiuwen_runtime.service import (
    EtcdLockBackend,
    LockBackendUnavailable,
    LockLost,
    MemoryLockBackend,
    RedisLockBackend,
    build_lock_backend,
)
from openjiuwen_runtime.service.context.locks.backends.etcd import parse_etcd_endpoint


@pytest.mark.unit
async def test_memory_backend_ttl_token_cas_and_cleanup():
    backend = MemoryLockBackend(prefix="test")
    first = await backend.try_acquire("job", ttl=0.03)
    assert first is not None
    assert first.key == "test:job"
    assert await backend.try_acquire("job", ttl=1) is None

    await asyncio.sleep(0.05)
    second = await backend.try_acquire("job", ttl=1)
    assert second is not None
    assert second.token != first.token
    assert await backend.release(first) is False
    with pytest.raises(LockLost):
        await backend.renew(first, ttl=1)
    assert await backend.release(second) is True


@pytest.mark.unit
async def test_redis_backend_uses_diagnostic_token_and_owner_cas():
    redis = fakeredis.aioredis.FakeRedis()
    backend = RedisLockBackend(
        redis,
        prefix="test",
        instance_id="instance-1",
        request_id="request-1",
    )
    first = await backend.try_acquire("job", ttl=1)
    assert first is not None
    assert '"instance_id":"instance-1"' in first.token
    assert await backend.try_acquire("job", ttl=1) is None
    await redis.set(first.key, "new-owner")
    assert await backend.release(first) is False
    assert await redis.get(first.key) == b"new-owner"


class _FakeLease:
    def __init__(self, client: "_FakeEtcd", lease_id: int, ttl: int) -> None:
        self.client = client
        self.id = lease_id
        self.ttl = ttl
        self.revoked = False

    async def refresh(self):
        if self.revoked:
            return None
        return SimpleNamespace(TTL=self.ttl)

    async def revoke(self):
        self.revoked = True
        for key, entry in list(self.client.values.items()):
            if entry.lease == self.id:
                del self.client.values[key]


class _FakeTransactions:
    @staticmethod
    def create(key):
        return _FakeCreateCompare(key)

    @staticmethod
    def value(key):
        return _FakeValueCompare(key)

    @staticmethod
    def put(key, value, lease=None):
        return ("put", key, value, lease)

    @staticmethod
    def delete(key):
        return ("delete", key)


class _FakeValueCompare:
    def __init__(self, key):
        self.key = key

    def __eq__(self, value):
        return ("value", self.key, value)


class _FakeCreateCompare:
    def __init__(self, key):
        self.key = key

    def __eq__(self, value):
        return ("create", self.key, value)


class _FakeEtcd:
    def __init__(self):
        self.transactions = _FakeTransactions()
        self.values = {}
        self.leases = []
        self.revision = 0
        self.next_lease = 1

    async def lease(self, ttl):
        lease = _FakeLease(self, self.next_lease, ttl)
        self.next_lease += 1
        self.leases.append(lease)
        return lease

    async def transaction(self, compare, success, failure):
        condition = compare[0]
        if condition[0] == "create":
            succeeded = condition[1] not in self.values
        else:
            entry = self.values.get(condition[1])
            succeeded = entry is not None and entry.value == condition[2]
        if succeeded:
            operation = success[0]
            if operation[0] == "put":
                self.revision += 1
                self.values[operation[1]] = SimpleNamespace(
                    value=operation[2],
                    lease=int(operation[3]),
                    create_revision=self.revision,
                )
            elif operation[0] == "delete":
                self.values.pop(operation[1], None)
        return succeeded, []

    async def get(self, key):
        entry = self.values.get(key)
        if entry is None:
            return None
        return SimpleNamespace(value=entry.value, create_revision=entry.create_revision)

    async def status(self):
        return SimpleNamespace()


@pytest.mark.unit
async def test_etcd_backend_lease_cas_and_fencing_revision():
    client = _FakeEtcd()
    backend = EtcdLockBackend(client, prefix="test")
    first = await backend.try_acquire("job", ttl=1)
    assert first is not None
    assert first.fencing_token == 1
    assert await backend.try_acquire("job", ttl=1) is None
    assert client.leases[-1].revoked is True
    renewed = await backend.renew(first, ttl=2)
    assert renewed.expires_at > first.expires_at
    await backend.release(first)

    second = await backend.try_acquire("job", ttl=1)
    assert second is not None
    assert second.fencing_token > first.fencing_token
    assert await backend.release(first) is False
    current = await client.get(second.key.encode())
    assert current is not None
    assert current.value == second.token.encode()
    await backend.close()
    assert await client.get(second.key.encode()) is None


@pytest.mark.unit
def test_lock_backend_factory_selection_and_auto_rules():
    memory = build_lock_backend("memory", key_prefix="test")
    assert isinstance(memory, MemoryLockBackend)
    redis = fakeredis.aioredis.FakeRedis()
    assert isinstance(build_lock_backend("redis", redis=redis), RedisLockBackend)
    assert isinstance(build_lock_backend("auto", redis=redis), RedisLockBackend)
    assert isinstance(build_lock_backend("auto", deploy_replicas=1), MemoryLockBackend)
    with pytest.raises(LockBackendUnavailable):
        build_lock_backend("auto", deploy_replicas=2)


@pytest.mark.unit
def test_etcd_endpoint_parsing():
    endpoint = parse_etcd_endpoint("https://etcd.internal:2380")
    assert endpoint.host == "etcd.internal"
    assert endpoint.port == 2380
    assert endpoint.tls is True
    assert parse_etcd_endpoint("127.0.0.1").port == 2379


@pytest.mark.integration
@pytest.mark.asyncio
async def test_etcd_real_integration_when_configured():
    endpoint = os.getenv("OPENJIUWEN_TEST_ETCD_ENDPOINT")
    if not endpoint:
        pytest.skip("OPENJIUWEN_TEST_ETCD_ENDPOINT is not configured")
    from openjiuwen_runtime.service.context.locks.backends.etcd import (
        create_etcd_client,
    )

    client = create_etcd_client(endpoint)
    await client.connect()
    backend = EtcdLockBackend(client, prefix=f"test:{uuid4().hex}", owns_client=True)
    try:
        credential = await backend.try_acquire("job", ttl=5)
        assert credential is not None
        assert credential.fencing_token is not None
        assert await backend.release(credential) is True
    finally:
        await backend.close()
