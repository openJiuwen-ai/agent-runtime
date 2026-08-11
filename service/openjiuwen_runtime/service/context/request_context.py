# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Request-scoped service context."""
from __future__ import annotations

import asyncio
import inspect
import logging
import math
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeAlias, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from ..envelope import Envelope, Metadata
from ..errors import DeadlineExceeded, FrameworkError, Interrupted
from .audit import AuditEvent
from .primitives.kv_store import KVStore

if TYPE_CHECKING:
    from .system_context import SystemContext


TRequest = TypeVar("TRequest")
_logger = logging.getLogger("openjiuwen_runtime.service")


class _RequestLogger(logging.Logger):
    """Logger carrying immutable request fields on every emitted record."""

    def __init__(self, base: logging.Logger, request_id: str, trace_id: str | None) -> None:
        super().__init__(base.name, level=logging.NOTSET)
        self.parent = base
        self.propagate = True
        self.extra = {"request_id": request_id, "trace_id": trace_id}

    # Keep the standard-library override signature required by logging.Logger.
    # pylint: disable=huawei-too-many-arguments
    def makeRecord(  # noqa: N802 - logging.Logger API
        self,
        name: str,
        level: int,
        fn: str,
        lno: int,
        msg: object,
        args: tuple[Any, ...],
        exc_info: Any,
        func: str | None = None,
        extra: dict[str, Any] | None = None,
        sinfo: str | None = None,
    ) -> logging.LogRecord:
        bound_extra = dict(self.extra)
        if extra:
            bound_extra.update(extra)
        return super().makeRecord(
            name, level, fn, lno, msg, args, exc_info, func, bound_extra, sinfo
        )
    # pylint: enable=huawei-too-many-arguments


CleanupCallback = Callable[[], Any]


@dataclass
class RequestContext(Generic[TRequest]):
    """Capabilities and metadata associated with one service request."""

    sysctx: SystemContext
    envelope: Envelope[TRequest] | None = None
    _metadata: Metadata | None = field(default=None, repr=False)
    lock_owner: str = ""
    logger: logging.Logger = field(default_factory=lambda: _logger)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    deadline: float | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    principal: Any = None
    _kv: KVStore | None = field(default=None, repr=False, compare=False)
    _idem: Any = field(default=None, repr=False, compare=False)
    _queue: Any = field(default=None, repr=False, compare=False)
    _pubsub: Any = field(default=None, repr=False, compare=False)
    _interrupt_reason: str | None = field(default=None, init=False, repr=False, compare=False)
    _cleanup_callbacks: list[CleanupCallback] = field(
        default_factory=list, init=False, repr=False, compare=False
    )
    _close_task: asyncio.Task[None] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.envelope is None and self._metadata is None:
            raise ValueError("envelope or metadata is required")
        if self.deadline is not None:
            if not isinstance(self.deadline, (int, float)):
                raise TypeError("deadline must be an absolute monotonic timestamp or None")
            if not math.isfinite(self.deadline):
                raise ValueError("deadline must be a finite monotonic timestamp")
        if not isinstance(self.logger, _RequestLogger):
            self.logger = _RequestLogger(self.logger, self.request_id, self.trace_id)

    @property
    def request(self) -> TRequest:
        """Return the validated request object bound to the envelope."""
        if self.envelope is None:
            raise FrameworkError("request is unavailable for a metadata-only context")
        return self.envelope.rawdata

    @property
    def metadata(self) -> Metadata:
        if self.envelope is not None:
            return self.envelope.metadata
        if self._metadata is None:  # Guarded by __post_init__.
            raise FrameworkError("request metadata is unavailable")
        return self._metadata

    @property
    def msg_type(self) -> str:
        if self.envelope is None:
            raise FrameworkError("message type is unavailable for a metadata-only context")
        return self.envelope.type

    @property
    def request_id(self) -> str:
        return self.metadata.request_id

    @property
    def user_id(self) -> str | None:
        return self.metadata.user_id

    @property
    def chat_id(self) -> str | None:
        return self.metadata.chat_id

    @property
    def session_id(self) -> str | None:
        return self.metadata.session_id

    @property
    def trace_id(self) -> str | None:
        return self.metadata.trace_id

    @property
    def bot_id(self) -> str | None:
        return self.metadata.bot_id

    @property
    def channel(self) -> str | None:
        return self.metadata.channel

    @property
    def instance_id(self) -> str | None:
        """Business instance identifier supplied by the request."""
        return self.metadata.instance_id

    @property
    def replica_id(self) -> str:
        """Identifier of the service process handling the request."""
        return self.sysctx.instance_id

    def remaining_seconds(self) -> float | None:
        """Return the non-negative duration remaining before the deadline."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def interrupt(self, reason: str | None = None) -> None:
        """Mark this request as interrupted and wake interruption waiters."""
        if self.cancel_event.is_set():
            return
        self._interrupt_reason = reason
        self.cancel_event.set()

    def check_interrupted(self) -> None:
        """Raise the lifecycle error currently preventing request work."""
        if self.cancel_event.is_set():
            raise Interrupted(self._interrupt_reason or "request interrupted")
        if self.deadline is not None and self.remaining_seconds() == 0:
            raise DeadlineExceeded("request deadline exceeded")

    async def wait_interrupted(self) -> None:
        """Wait until explicit interruption or the request deadline."""
        while not self.cancel_event.is_set():
            remaining = self.remaining_seconds()
            if remaining is None:
                await self.cancel_event.wait()
                return
            if remaining == 0:
                return
            try:
                await asyncio.wait_for(self.cancel_event.wait(), timeout=remaining)
            except TimeoutError:
                continue

    def add_cleanup(self, callback: CleanupCallback) -> None:
        """Register a synchronous or asynchronous request cleanup callback."""
        if not callable(callback):
            raise TypeError("cleanup callback must be callable")
        if self._close_task is not None:
            raise RuntimeError("request context is already closing")
        self._cleanup_callbacks.append(callback)

    register_cleanup = add_cleanup

    @property
    def closed(self) -> bool:
        return self._close_task is not None and self._close_task.done()

    async def close(self) -> None:
        """Run all request cleanup callbacks once in LIFO order."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._run_cleanup(), name=f"request-cleanup:{self.request_id}"
            )
        task = self._close_task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    async def _run_cleanup(self) -> None:
        while self._cleanup_callbacks:
            callback = self._cleanup_callbacks.pop()
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except BaseException:  # noqa: BLE001 - one cleanup cannot skip the rest
                self.logger.exception("request cleanup failed: callback=%r", callback)

    @property
    def db(self) -> Any:
        """Return the configured DB handler after checking request state."""
        return self.require_db()

    @property
    def redis(self) -> Any:
        """Return the shared asynchronous Redis client for this request.

        The client is owned by :class:`SystemContext` and is only borrowed by
        the request. Callers may use the complete ``redis.asyncio`` API, but
        must not close the client from request or handler code.
        """
        return self.require_redis()

    def require_redis(self) -> Any:
        """Require the shared asynchronous Redis client for an active request."""
        self.check_interrupted()
        return self.sysctx.require_redis()

    def require_db(self) -> Any:
        """Require a DB handler for this active request."""
        self.check_interrupted()
        return self.sysctx.require_db()

    async def db_create(self, table_name: str, data: dict[str, Any]) -> Any:
        """Create a record using an independent DBHandler operation."""
        return await self.require_db().create(table_name, data)

    async def db_get(self, table_name: str, filters: dict[str, Any]) -> Any:
        """Get one record using an independent DBHandler operation."""
        return await self.require_db().get(table_name, filters)

    async def db_update(
        self,
        table_name: str,
        filters: dict[str, Any],
        data: dict[str, Any],
    ) -> Any:
        """Update records using an independent DBHandler operation."""
        return await self.require_db().update(table_name, filters, data)

    async def db_delete(self, table_name: str, filters: dict[str, Any]) -> bool:
        """Delete records using an independent DBHandler operation."""
        return await self.require_db().delete(table_name, filters)

    async def db_list(
        self,
        table_name: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """List records using an independent DBHandler operation."""
        return await self.require_db().list_records(
            table_name,
            filters=filters,
            limit=limit,
            offset=offset,
        )

    async def db_count(
        self,
        table_name: str,
        filters: dict[str, Any] | None = None,
    ) -> int:
        """Count records using an independent DBHandler operation."""
        return await self.require_db().count_records(table_name, filters=filters)

    @property
    def kv(self) -> KVStore:
        """Distributed dictionary and session storage."""
        if self._kv is None:
            self._kv = KVStore(
                self.sysctx.require_redis(), prefix=self.sysctx.namespace("kv")
            )
        return self._kv

    @property
    def idempotency(self):
        """Global request-id deduplication and result replay."""
        if self._idem is None:
            from .primitives.idempotency import Idempotency

            self._idem = Idempotency(
                self.sysctx.require_redis(), prefix=self.sysctx.namespace("idem")
            )
        return self._idem

    @property
    def queue(self):
        """Durable cross-replica queue backed by Redis Streams."""
        if self._queue is None:
            from .primitives.stream_queue import StreamQueue

            self._queue = StreamQueue(
                self.sysctx.require_redis(), prefix=self.sysctx.namespace("queue")
            )
        return self._queue

    @property
    def pubsub(self):
        """Transient fan-out backed by Redis Pub/Sub."""
        if self._pubsub is None:
            from .primitives.pubsub import PubSub

            self._pubsub = PubSub(
                self.sysctx.require_redis(), prefix=self.sysctx.namespace("pubsub")
            )
        return self._pubsub

    def lock(
        self,
        key: str,
        *,
        ttl: float = 30,
        timeout: float = 0,
        renew_interval: float | None = None,
    ):
        """Create a distributed lock bound to this request owner."""
        from .primitives.lock import DistributedLock

        self.check_interrupted()
        return DistributedLock(
            self.sysctx.require_redis(),
            key,
            owner=self.lock_owner,
            ttl=ttl,
            timeout=timeout,
            prefix=self.sysctx.namespace("lock"),
            renew_interval=renew_interval,
            check_interrupted=self.check_interrupted,
        )

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Yield one transaction session shared by explicit session operations.

        The ``db_*`` helpers use DBHandler-owned sessions and remain independent
        from this transaction.
        """
        self.check_interrupted()
        async with self.sysctx.transaction() as session:
            yield session

    async def audit(
        self,
        action: str,
        *,
        outcome: str = "success",
        actor: str | None = None,
        resource: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Write an audit event populated from this request context."""
        self.check_interrupted()
        msg_type = self.envelope.type if self.envelope is not None else None
        resolved_actor = actor or self._principal_identifier() or self.user_id
        event = AuditEvent(
            action=action,
            outcome=outcome,
            actor=resolved_actor,
            user_id=self.user_id,
            resource=resource or msg_type,
            request_id=self.request_id,
            trace_id=self.trace_id,
            session_id=self.session_id,
            msg_type=msg_type,
            instance_id=self.instance_id,
            replica_id=self.replica_id,
            details=dict(details or {}),
        )
        await self.sysctx.audit(event)

    def _principal_identifier(self) -> str | None:
        principal = self.principal
        if principal is None:
            return None
        if isinstance(principal, str):
            return principal
        if isinstance(principal, Mapping):
            value = (
                principal.get("user_id")
                or principal.get("subject")
                or principal.get("sub")
                or principal.get("id")
            )
            return str(value) if value is not None else None
        for name in ("user_id", "subject", "sub", "id"):
            value = getattr(principal, name, None)
            if value is not None:
                return str(value)
        return None


TypedAppContext: TypeAlias = RequestContext


__all__ = ["RequestContext", "TypedAppContext"]
