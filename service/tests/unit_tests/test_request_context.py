# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""DB, Redis, transaction, audit, and lock capabilities on RequestContext."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import fakeredis.aioredis
import pytest

from openjiuwen_runtime.service import (
    AuditEvent,
    DatabaseUnavailable,
    Envelope,
    Interrupted,
    LoggingAuditLogger,
    Metadata,
    RedisUnavailable,
    SystemContext,
)


def _env() -> Envelope[dict[str, Any]]:
    return Envelope(
        type="users/update",
        metadata=Metadata(
            request_id="request-1",
            trace_id="trace-1",
            user_id="metadata-user",
            session_id="session-1",
            instance_id="workflow-1",
        ),
        rawdata={"id": 1},
    )


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
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.sessions: list[_FakeSession] = []

    def session_factory(self) -> _FakeSession:
        session = _FakeSession()
        self.sessions.append(session)
        return session

    async def create(self, table_name: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create", table_name, data))
        return dict(data)

    async def get(self, table_name: str, filters: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("get", table_name, filters))
        return dict(filters)

    async def update(
        self,
        table_name: str,
        filters: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(("update", table_name, filters, data))
        return {**filters, **data}

    async def delete(self, table_name: str, filters: dict[str, Any]) -> bool:
        self.calls.append(("delete", table_name, filters))
        return True

    async def list_records(
        self,
        table_name: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self.calls.append(("list", table_name, filters, limit, offset))
        return [dict(filters or {})]

    async def count_records(
        self,
        table_name: str,
        filters: dict[str, Any] | None = None,
    ) -> int:
        self.calls.append(("count", table_name, filters))
        return 1


class _CaptureAuditLogger:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> None:
        self.events.append(event)


@dataclass
class _Principal:
    user_id: str


@pytest.mark.unit
def test_system_context_requires_configured_resources():
    sysctx = SystemContext()

    with pytest.raises(DatabaseUnavailable):
        sysctx.require_db()
    with pytest.raises(RedisUnavailable):
        sysctx.require_redis()


@pytest.mark.unit
async def test_db_helpers_delegate_as_independent_operations():
    db = _FakeDb()
    ctx = SystemContext(db=db).for_request(_env())

    assert ctx.db is db
    assert ctx.require_db() is db
    assert await ctx.db_create("users", {"id": 1}) == {"id": 1}
    assert await ctx.db_get("users", {"id": 1}) == {"id": 1}
    assert await ctx.db_update("users", {"id": 1}, {"name": "new"}) == {
        "id": 1,
        "name": "new",
    }
    assert await ctx.db_delete("users", {"id": 1}) is True
    assert await ctx.db_list("users", {"active": True}, limit=5, offset=2) == [
        {"active": True}
    ]
    assert await ctx.db_count("users", {"active": True}) == 1
    assert [call[0] for call in db.calls] == [
        "create",
        "get",
        "update",
        "delete",
        "list",
        "count",
    ]


@pytest.mark.unit
async def test_db_helpers_check_request_state_and_missing_database():
    missing = SystemContext().for_request(_env())
    with pytest.raises(DatabaseUnavailable):
        await missing.db_get("users", {"id": 1})

    interrupted = SystemContext(db=_FakeDb()).for_request(_env())
    interrupted.interrupt("stop database work")
    with pytest.raises(Interrupted, match="stop database work"):
        await interrupted.db_count("users")


@pytest.mark.unit
async def test_request_context_exposes_shared_async_redis_client(mocker):
    redis = fakeredis.aioredis.FakeRedis()
    close_spy = mocker.spy(redis, "aclose")
    sysctx = SystemContext(redis=redis)
    first = sysctx.for_request(_env())
    second = sysctx.for_request(_env())

    assert first.redis is redis
    assert first.require_redis() is redis
    assert second.redis is redis

    await first.redis.hset("user:1", mapping={"name": "Alice"})
    await first.redis.zadd("user:scores", {"user:1": 10})

    assert await second.redis.hget("user:1", "name") == b"Alice"
    assert await second.redis.zscore("user:scores", "user:1") == 10

    await first.close()
    close_spy.assert_not_awaited()
    assert await redis.ping() is True


@pytest.mark.unit
def test_request_context_redis_requires_configuration_and_active_request():
    missing = SystemContext().for_request(_env())
    with pytest.raises(RedisUnavailable):
        _ = missing.redis
    with pytest.raises(RedisUnavailable):
        missing.require_redis()

    redis = fakeredis.aioredis.FakeRedis()
    interrupted = SystemContext(redis=redis).for_request(_env())
    interrupted.interrupt("stop redis work")

    with pytest.raises(Interrupted, match="stop redis work"):
        _ = interrupted.redis
    with pytest.raises(Interrupted, match="stop redis work"):
        interrupted.require_redis()


@pytest.mark.unit
async def test_transaction_yields_one_session_and_db_helpers_stay_independent():
    db = _FakeDb()
    ctx = SystemContext(db=db).for_request(_env())

    async with ctx.transaction() as session:
        same_session = session
        await ctx.db_create("users", {"id": 1})

    assert session is same_session is db.sessions[0]
    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True
    assert db.calls == [("create", "users", {"id": 1})]


@pytest.mark.unit
async def test_request_audit_populates_context_fields_and_principal_actor():
    audit = _CaptureAuditLogger()
    ctx = SystemContext(
        audit_logger=audit,
        instance_id="replica-1",
    ).for_request(_env())
    ctx.principal = _Principal(user_id="principal-user")
    details = {"changed": ["name"]}

    await ctx.audit("user.updated", details=details)

    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.action == "user.updated"
    assert event.actor == "principal-user"
    assert event.user_id == "metadata-user"
    assert event.resource == "users/update"
    assert event.request_id == "request-1"
    assert event.trace_id == "trace-1"
    assert event.session_id == "session-1"
    assert event.msg_type == "users/update"
    assert event.instance_id == "workflow-1"
    assert event.replica_id == "replica-1"
    assert event.details == details
    assert event.details is not details


@pytest.mark.unit
async def test_default_audit_logger_emits_structured_event(caplog):
    logger = logging.getLogger("test.audit")
    logger.setLevel(logging.INFO)
    ctx = SystemContext(logger=logger).for_request(_env())

    with caplog.at_level(logging.INFO, logger=logger.name):
        await ctx.audit("user.read", actor="explicit-actor", resource="user:1")

    record = next(record for record in caplog.records if "audit action=user.read" in record.message)
    assert record.audit["actor"] == "explicit-actor"
    assert record.audit["resource"] == "user:1"
    assert record.audit["request_id"] == "request-1"


@pytest.mark.unit
def test_logging_audit_logger_accepts_named_level():
    audit = LoggingAuditLogger(level="WARNING")

    assert audit.level == logging.WARNING


@pytest.mark.unit
async def test_lock_checks_interruption_at_acquisition_time():
    redis = fakeredis.aioredis.FakeRedis()
    ctx = SystemContext(redis=redis).for_request(_env())
    lock = ctx.lock("user:1")
    ctx.interrupt("request cancelled")

    with pytest.raises(Interrupted, match="request cancelled"):
        async with lock:
            pass
    assert await redis.exists(lock.key) == 0
