# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Executable coverage for the context capabilities example."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from examples.context_capabilities_app import USER_TABLE, app
from openjiuwen_runtime.service import (
    AuditEvent,
    Envelope,
    LockCapabilities,
    LockCredential,
    MemoryCacheBackend,
    Metadata,
    ServiceConfig,
    build_system_context,
)
from openjiuwen_runtime.service.routing.result import StreamResult, UnaryResult


class _FencingLockBackend:
    capabilities = LockCapabilities(distributed=True, fencing=True)

    def __init__(self) -> None:
        self.counter = 0
        self.held: dict[str, LockCredential] = {}

    async def ping(self) -> bool:
        return True

    async def try_acquire(self, key: str, ttl: float) -> LockCredential | None:
        if key in self.held:
            return None
        self.counter += 1
        now = time.monotonic()
        credential = LockCredential(
            key=key,
            token=uuid4().hex,
            backend="test-fencing",
            lease_id=self.counter,
            fencing_token=self.counter,
            acquired_at=now,
            expires_at=now + ttl,
        )
        self.held[key] = credential
        return credential

    async def renew(self, credential: LockCredential, ttl: float) -> LockCredential:
        current = self.held.get(credential.key)
        if current is None or current.token != credential.token:
            raise RuntimeError("lock is no longer held")
        renewed = LockCredential(
            key=credential.key,
            token=credential.token,
            backend=credential.backend,
            lease_id=credential.lease_id,
            fencing_token=credential.fencing_token,
            acquired_at=credential.acquired_at,
            expires_at=time.monotonic() + ttl,
        )
        self.held[credential.key] = renewed
        return renewed

    async def release(self, credential: LockCredential) -> bool:
        current = self.held.get(credential.key)
        if current is None or current.token != credential.token:
            return False
        del self.held[credential.key]
        return True


class _CaptureAuditLogger:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> None:
        self.events.append(event)


def _envelope(msg_type: str, rawdata: dict[str, Any]) -> Envelope[dict[str, Any]]:
    return Envelope(
        type=msg_type,
        metadata=Metadata(
            request_id=uuid4().hex,
            user_id="example-tester",
            trace_id=uuid4().hex,
        ),
        rawdata=rawdata,
    )


async def _dispatch(system, msg_type: str, rawdata: dict[str, Any]) -> UnaryResult:
    envelope = _envelope(msg_type, rawdata)
    context = system.for_request(envelope)
    try:
        result = await app.dispatch(envelope, context)
        assert isinstance(result, UnaryResult)
        return result
    finally:
        await context.close()


@pytest.mark.unit
async def test_context_capabilities_user_flow():
    lock_backend = _FencingLockBackend()
    cache_backend = MemoryCacheBackend(prefix="context-example-test")
    audit_logger = _CaptureAuditLogger()
    db_path = Path(__file__).parent / f".context-capabilities-{uuid4().hex}.db"
    settings = ServiceConfig(
        redis_url="",
        lock_backend="memory",
        cache_backend="memory",
        db_type="sqlite",
        db_name=str(db_path),
    )
    system = build_system_context(
        settings,
        lock_backend=lock_backend,
        cache_backend=cache_backend,
        table_definitions=(USER_TABLE,),
    )
    system.set_audit_logger(audit_logger)
    await system.start()
    try:
        created = await _dispatch(
            system,
            "users/create",
            {"email": "alice@example.com", "name": "Alice"},
        )
        assert created.response.ok is True
        user_id = created.response.rawdata["id"]

        await cache_backend.clear_namespace()
        uncached = await _dispatch(system, "users/get", {"id": user_id})
        cached = await _dispatch(system, "users/get", {"id": user_id})
        assert uncached.response.rawdata["cache_hit"] is False
        assert cached.response.rawdata["cache_hit"] is True

        updated = await _dispatch(
            system,
            "users/update",
            {"id": user_id, "name": "Alice Chen"},
        )
        assert updated.response.ok is True
        assert updated.response.rawdata["name"] == "Alice Chen"
        assert updated.response.rawdata["fence_token"] == 1
        assert lock_backend.held == {}

        after_update = await _dispatch(system, "users/get", {"id": user_id})
        assert after_update.response.rawdata["cache_hit"] is False
        assert after_update.response.rawdata["user"]["name"] == "Alice Chen"

        removed = await _dispatch(system, "users/remove", {"id": user_id})
        missing = await _dispatch(system, "users/get", {"id": user_id})
        assert removed.response.rawdata == {"id": user_id, "removed": True}
        assert missing.response.ok is False
        assert missing.response.error_code == "not_found"
        assert [event.action for event in audit_logger.events] == [
            "users.create",
            "users.update",
            "users.remove",
        ]
    finally:
        await system.stop()
        await cache_backend.close()
        db_path.unlink(missing_ok=True)


@pytest.mark.unit
async def test_context_capabilities_manual_lock_and_stream():
    lock_backend = _FencingLockBackend()
    cache_backend = MemoryCacheBackend(prefix="context-example-stream-test")
    settings = ServiceConfig(
        redis_url="",
        lock_backend="memory",
        cache_backend="memory",
    )
    system = build_system_context(
        settings,
        lock_backend=lock_backend,
        cache_backend=cache_backend,
    )
    await system.start()
    try:
        manual = await _dispatch(
            system,
            "locks/manual",
            {"key": "job:daily", "ttl": 5, "wait_timeout": 0},
        )
        assert manual.response.ok is True
        assert manual.response.rawdata["released"] is True
        assert manual.response.rawdata["renewed"]["fencing_token"] == 1
        assert lock_backend.held == {}

        envelope = _envelope(
            "chat",
            {"text": "hello service", "delay_seconds": 0},
        )
        context = system.for_request(envelope)
        result = await app.dispatch(envelope, context)
        assert isinstance(result, StreamResult)
        chunks = [chunk async for chunk in result.chunks]
        await result.aclose()

        assert [chunk.rawdata["text"] for chunk in chunks] == ["hello", "service"]
        assert chunks[-1].is_final is True
        assert context.closed is True
    finally:
        await system.stop()
        await cache_backend.close()
