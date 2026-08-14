# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""统一锁凭证、租约状态和请求生命周期测试。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Callable

import pytest
import fakeredis.aioredis

from openjiuwen_runtime.service import (
    DeadlineExceeded,
    Envelope,
    Interrupted,
    InvalidLockLease,
    LeaseState,
    LockAcquireTimeout,
    LockCapabilities,
    LockCredential,
    LockLost,
    LockManager,
    Metadata,
    RequestContext,
    SystemContext,
)
from openjiuwen_runtime.service.context.primitives.lock import RedisLockBackend


class _Backend:
    capabilities = LockCapabilities(distributed=True, fencing=False)

    def __init__(self) -> None:
        self.held: dict[str, str] = {}
        self.sequence = 0
        self.renew_count = 0
        self.release_count = 0
        self.fail_renew = False
        self.after_acquire: Callable[[], None] | None = None

    async def try_acquire(self, key: str, ttl: float) -> LockCredential | None:
        if key in self.held:
            return None
        self.sequence += 1
        token = f"token-{self.sequence}"
        self.held[key] = token
        now = time.monotonic()
        credential = LockCredential(
            key=key,
            token=token,
            backend="fake",
            lease_id=None,
            fencing_token=None,
            acquired_at=now,
            expires_at=now + ttl,
        )
        if self.after_acquire is not None:
            self.after_acquire()
        return credential

    async def renew(self, credential: LockCredential, ttl: float) -> LockCredential:
        if self.fail_renew or self.held.get(credential.key) != credential.token:
            raise LockLost(f"lock {credential.key!r} was lost")
        self.renew_count += 1
        await asyncio.sleep(0)
        return replace(credential, expires_at=time.monotonic() + ttl)

    async def release(self, credential: LockCredential) -> bool:
        self.release_count += 1
        await asyncio.sleep(0)
        if self.held.get(credential.key) != credential.token:
            return False
        del self.held[credential.key]
        return True


class _BlockingAcquireBackend(_Backend):
    def __init__(self) -> None:
        super().__init__()
        self.acquired = asyncio.Event()
        self.finish = asyncio.Event()

    async def try_acquire(self, key: str, ttl: float) -> LockCredential | None:
        credential = await super().try_acquire(key, ttl)
        self.acquired.set()
        await self.finish.wait()
        return credential


def _env(request_id: str = "request-1") -> Envelope:
    return Envelope(type="work", metadata=Metadata(request_id=request_id), rawdata={})


def _context(backend: _Backend, *, timeout: float = 0) -> RequestContext[dict]:
    sysctx = SystemContext(lock_backend=backend, request_timeout_seconds=timeout)
    return sysctx.for_request(_env())


@pytest.mark.unit
async def test_manual_renew_replaces_credential_and_release_is_idempotent():
    backend = _Backend()
    manager = LockManager(backend)
    lease = await manager.acquire("job", ttl=1, auto_renew=False)
    original = lease.credential

    renewed = await lease.renew()
    assert renewed is lease.credential
    assert renewed is not original
    assert renewed.expires_at >= original.expires_at
    assert backend.renew_count == 1
    assert await lease.release() is True
    assert await lease.release() is True
    assert backend.release_count == 1
    assert lease.state is LeaseState.RELEASED
    with pytest.raises(InvalidLockLease):
        lease.ensure_valid()


@pytest.mark.unit
async def test_wait_timeout_uses_specific_compatible_error():
    backend = _Backend()
    holder = LockManager(backend)
    waiter = LockManager(backend)
    lease = await holder.acquire("job", auto_renew=False)

    with pytest.raises(LockAcquireTimeout):
        await waiter.acquire("job", wait_timeout=0.03, auto_renew=False)
    await lease.release()


@pytest.mark.unit
async def test_cancel_waiting_wakes_without_waiting_for_lock_ttl():
    backend = _Backend()
    first = _context(backend)
    second = _context(backend)
    lease = await first.locks.acquire("job", auto_renew=False)
    waiting = asyncio.create_task(second.locks.acquire("job", wait_timeout=5))

    await asyncio.sleep(0.02)
    second.interrupt("client cancelled")
    with pytest.raises(Interrupted, match="client cancelled"):
        await asyncio.wait_for(waiting, timeout=0.5)
    await lease.release()
    await first.close()
    await second.close()


@pytest.mark.unit
async def test_success_boundary_cancellation_compensates_acquired_credential():
    backend = _Backend()
    ctx = _context(backend)

    def interrupt_after_acquire() -> None:
        ctx.interrupt("cancelled at acquire boundary")

    backend.after_acquire = interrupt_after_acquire

    with pytest.raises(Interrupted, match="acquire boundary"):
        await ctx.locks.acquire("job", auto_renew=False)
    assert backend.held == {}
    assert backend.release_count == 1
    assert ctx.locks.active_leases == ()
    await ctx.close()


@pytest.mark.unit
async def test_task_cancellation_after_backend_success_compensates_credential():
    backend = _BlockingAcquireBackend()
    manager = LockManager(backend)
    acquiring = asyncio.create_task(manager.acquire("job", auto_renew=False))
    await backend.acquired.wait()

    acquiring.cancel()
    backend.finish.set()
    with pytest.raises(asyncio.CancelledError):
        await acquiring
    assert backend.held == {}
    assert backend.release_count == 1


@pytest.mark.unit
async def test_request_deadline_stops_lock_waiting():
    backend = _Backend()
    holder = _context(backend)
    waiter = _context(backend, timeout=0.03)
    lease = await holder.locks.acquire("job", auto_renew=False)

    with pytest.raises(DeadlineExceeded):
        await waiter.locks.acquire("job", wait_timeout=2)
    await lease.release()
    await holder.close()
    await waiter.close()


@pytest.mark.unit
async def test_auto_renew_failure_marks_lost_and_interrupts_request():
    backend = _Backend()
    ctx = _context(backend)
    lease = await ctx.locks.acquire("job", ttl=0.05, auto_renew=True, renew_ratio=0.2)
    backend.fail_renew = True

    await asyncio.wait_for(lease.wait_lost(), timeout=0.5)
    assert lease.state is LeaseState.LOST
    with pytest.raises(Interrupted, match="lost"):
        ctx.check_interrupted()
    with pytest.raises(InvalidLockLease):
        lease.ensure_valid()
    await ctx.close()


@pytest.mark.unit
async def test_concurrent_renew_and_release_finish_in_released_state():
    backend = _Backend()
    lease = await LockManager(backend).acquire("job", auto_renew=False)

    results = await asyncio.gather(
        lease.renew(), lease.release(), return_exceptions=True
    )
    assert not any(isinstance(result, BaseException) for result in results)
    assert lease.state is LeaseState.RELEASED
    assert backend.held == {}


@pytest.mark.unit
async def test_request_close_releases_explicit_leases_and_stops_renewal():
    backend = _Backend()
    ctx = _context(backend)
    lease = await ctx.locks.acquire("job", ttl=0.05, renew_ratio=0.2)

    await ctx.close()
    assert lease.state is LeaseState.RELEASED
    assert backend.held == {}
    assert ctx.locks.closed is True


@pytest.mark.unit
async def test_redis_backend_uses_unique_credential_and_owner_checked_cas():
    redis = fakeredis.aioredis.FakeRedis()
    backend = RedisLockBackend(redis, prefix="test:lock")
    first = await backend.try_acquire("job", ttl=1)
    assert first is not None
    assert first.key == "test:lock:job"
    assert await backend.try_acquire("job", ttl=1) is None

    renewed = await backend.renew(first, ttl=2)
    assert renewed.expires_at > first.expires_at
    await redis.set(first.key, "new-owner")
    assert await backend.release(first) is False
    assert await redis.get(first.key) == b"new-owner"
