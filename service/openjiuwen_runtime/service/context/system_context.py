# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SystemContext（设计 §8）。

- 进程级 SystemContext（lifespan 创建/释放）：db / redis / settings / logger / 原语工厂。
- 请求级 RequestContext 由 ``for_request(envelope)`` 派生。
- 事务：``async with ctx.transaction() as s`` 取 SQLAlchemy session（多操作原子）。
- 硬约束：handler 禁止读写模块级可变状态——无内存状态的多副本。
"""

from __future__ import annotations

import logging
import math
import socket
import time
from dataclasses import replace
from inspect import isawaitable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable, TypeVar, overload
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ServiceConfig
from ..envelope import Envelope, Metadata
from ..errors import (
    CacheUnavailable,
    DatabaseUnavailable,
    FrameworkError,
    LockBackendUnavailable,
    RedisUnavailable,
)
from .audit import AuditEvent, AuditLogger, LoggingAuditLogger, NoopAuditLogger
from .request_context import RequestContext

_logger = logging.getLogger("openjiuwen_runtime.service")
TRequest = TypeVar("TRequest")


class SystemContext:
    """进程级系统能力容器 + 请求级上下文工厂。"""

    def __init__(
        self,
        redis: Any = None,
        db: Any = None,
        settings: Any = None,
        *,
        key_prefix: str = "service",
        instance_id: str | None = None,
        logger: logging.Logger | None = None,
        audit_logger: AuditLogger | None = None,
        audit: AuditLogger | None = None,
        etcd: Any = None,
        lock_backend: Any = None,
        cache_backend: Any = None,
        cache: Any = None,
        table_definitions: Iterable[Any] | None = None,
        request_timeout_seconds: float | None = None,
        _owns_db: bool | None = None,
        _owns_redis: bool = False,
        _owns_lock_backend: bool | None = None,
        _owns_cache_backend: bool | None = None,
    ) -> None:
        self.redis = redis
        self.db = db
        self.settings = settings
        if request_timeout_seconds is None:
            request_timeout_seconds = self._request_timeout_from_settings(settings)
        if not math.isfinite(request_timeout_seconds) or request_timeout_seconds < 0:
            raise ValueError(
                "request_timeout_seconds must be a finite non-negative number"
            )
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.key_prefix = key_prefix or ""
        self.instance_id = instance_id or f"{socket.gethostname()}:{uuid4().hex[:8]}"
        self.logger = logger or _logger
        if audit_logger is not None and audit is not None:
            raise ValueError("audit_logger and audit cannot both be provided")
        self.audit_logger: AuditLogger = (
            audit_logger or audit or LoggingAuditLogger(self.logger)
        )
        self.lock_backend = lock_backend
        self.etcd = etcd if etcd is not None else getattr(lock_backend, "_client", None)
        if cache_backend is not None and cache is not None:
            raise ValueError("cache_backend and cache cannot both be provided")
        self.cache_backend = cache_backend if cache_backend is not None else cache
        self.table_definitions = tuple(table_definitions or ())
        self._owns_db = db is not None if _owns_db is None else bool(_owns_db)
        self._owns_redis = _owns_redis
        self._owns_lock_backend = (
            lock_backend is not None
            if _owns_lock_backend is None
            else bool(_owns_lock_backend)
        )
        self._owns_etcd = bool(getattr(lock_backend, "_owns_client", False))
        self._owns_cache_backend = (
            self.cache_backend is not None
            if _owns_cache_backend is None
            else bool(_owns_cache_backend)
        )
        self._started = False
        self._stopped = False
        self._active_resources: list[str] = []

    @staticmethod
    def _request_timeout_from_settings(settings: Any) -> float:
        default = ServiceConfig.from_env().request_timeout_seconds
        if settings is None:
            return default
        if isinstance(settings, dict):
            value = settings.get("request_timeout_seconds", default)
        else:
            value = getattr(settings, "request_timeout_seconds", default)
        return float(value)

    # -------------------------------------------------------------- 命名空间
    def namespace(self, suffix: str) -> str:
        """Return the configured namespace for a service capability."""
        return f"{self.key_prefix}:{suffix}" if self.key_prefix else suffix

    # -------------------------------------------------------------- 能力检查
    def require_db(self) -> Any:
        """Return the configured DB handler or raise a framework error."""
        if self.db is None:
            raise DatabaseUnavailable("database handler is not configured")
        return self.db

    def require_redis(self) -> Any:
        """Return the configured Redis client or raise a framework error."""
        if self.redis is None:
            raise RedisUnavailable("redis client is not configured")
        return self.redis

    def require_cache(self) -> Any:
        """Return the configured cache backend or raise a framework error."""
        if self.cache_backend is None:
            raise CacheUnavailable("cache backend is not configured")
        return self.cache_backend

    def set_db(self, db: Any, *, owned: bool = False) -> None:
        self.db = db
        self._owns_db = bool(db is not None and owned)

    def set_redis(self, redis: Any, *, owned: bool = False) -> None:
        self.redis = redis
        self._owns_redis = bool(redis is not None and owned)

    def set_lock_backend(self, backend: Any, *, owned: bool = True) -> None:
        self.lock_backend = backend
        self.etcd = getattr(backend, "_client", None)
        self._owns_lock_backend = bool(backend is not None and owned)
        self._owns_etcd = bool(getattr(backend, "_owns_client", False))

    def set_cache_backend(self, backend: Any, *, owned: bool = True) -> None:
        self.cache_backend = backend
        self._owns_cache_backend = bool(backend is not None and owned)

    def set_audit_logger(self, audit_logger: AuditLogger | None) -> None:
        """Replace the process audit sink; ``None`` selects a no-op sink."""
        self.audit_logger = audit_logger or NoopAuditLogger()

    set_audit = set_audit_logger

    async def audit(self, event: AuditEvent) -> None:
        """Write an audit event through the configured sink."""
        await self.audit_logger.write(event)

    # -------------------------------------------------------------- 生命周期
    async def start(self) -> None:
        if self._started:
            return
        self._stopped = False
        self._active_resources.clear()
        try:
            if self.db is not None:
                self._active_resources.append("db")
                if self._owns_db:
                    init_database = getattr(self.db, "init_database", None)
                    if callable(init_database):
                        await self._call(init_database)
                    connect = getattr(self.db, "connect", None)
                    if callable(connect):
                        await self._call(connect)
                init_table = getattr(self.db, "init_table", None)
                if callable(init_table):
                    for table_definition in self.table_definitions:
                        await self._call(init_table, table_definition)
                if not await self._db_ready():
                    raise DatabaseUnavailable("database readiness check failed")

            if self.redis is not None:
                self._active_resources.append("redis")
                if not await self._ping(self.redis):
                    raise RedisUnavailable("Redis readiness check failed")

            if self.lock_backend is not None:
                self._active_resources.append("lock")
                connect = getattr(self.lock_backend, "connect", None)
                if self._owns_lock_backend and callable(connect):
                    await self._call(connect)
                if not await self._ping(self.lock_backend):
                    raise LockBackendUnavailable("lock backend readiness check failed")
            self._validate_lock_capabilities()

            if self.cache_backend is not None:
                self._active_resources.append("cache")
                if not await self._ping(self.cache_backend):
                    raise CacheUnavailable("cache backend readiness check failed")
            self._started = True
        except BaseException:
            await self._stop_resources(suppress_errors=True)
            self._stopped = True
            raise

    async def stop(self) -> None:
        if not self._started and not self._active_resources and self._stopped:
            return
        if not self._active_resources:
            resources = (
                ("db", self.db),
                ("redis", self.redis),
                ("lock", self.lock_backend),
                ("cache", self.cache_backend),
            )
            for name, resource in resources:
                if resource is not None:
                    self._active_resources.append(name)
        await self._stop_resources(suppress_errors=False)
        self._started = False
        self._stopped = True

    async def readiness(self) -> dict[str, bool | None]:
        """Return current readiness for every configured process resource."""
        statuses: dict[str, bool | None] = {
            "db": None,
            "redis": None,
            "lock": None,
            "cache": None,
        }
        checks = (
            ("db", self.db, self._db_ready),
            ("redis", self.redis, lambda: self._ping(self.redis)),
            ("lock", self.lock_backend, lambda: self._ping(self.lock_backend)),
            ("cache", self.cache_backend, lambda: self._ping(self.cache_backend)),
        )
        for name, resource, check in checks:
            if resource is None:
                continue
            try:
                statuses[name] = bool(await check())
            except Exception:  # noqa: BLE001 - readiness reports failures
                statuses[name] = False
        statuses["ready"] = all(value is not False for value in statuses.values())
        return statuses

    async def _stop_resources(self, *, suppress_errors: bool) -> None:
        errors: list[BaseException] = []
        active = set(self._active_resources)
        for name in ("cache", "lock", "redis", "db"):
            if name not in active:
                continue
            try:
                await self._close_resource(name)
            except BaseException as exc:  # noqa: BLE001 - continue reverse cleanup
                errors.append(exc)
                self.logger.exception("resource cleanup failed: resource=%s", name)
        self._active_resources.clear()
        self._started = False
        if errors and not suppress_errors:
            raise errors[0]

    async def _close_resource(self, name: str) -> None:
        if (
            name == "cache"
            and self.cache_backend is not None
            and self._owns_cache_backend
        ):
            await self._call(self.cache_backend.close)
            return
        if name == "lock" and self.lock_backend is not None and self._owns_lock_backend:
            close = getattr(self.lock_backend, "close", None)
            if callable(close):
                await self._call(close)
            return
        if name == "redis" and self.redis is not None and self._owns_redis:
            close = getattr(self.redis, "aclose", None) or getattr(
                self.redis, "close", None
            )
            if callable(close):
                await self._call(close)
            return
        if name == "db" and self.db is not None and self._owns_db:
            disconnect = getattr(self.db, "disconnect", None)
            if callable(disconnect):
                await self._call(disconnect)

    async def _db_ready(self) -> bool:
        if self.db is None:
            return False
        ping = getattr(self.db, "ping", None)
        if callable(ping):
            return bool(await self._call(ping))
        engine = getattr(self.db, "engine", None)
        if engine is not None and callable(getattr(engine, "connect", None)):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        session_factory = getattr(self.db, "session_factory", None)
        if callable(session_factory):
            session = session_factory()
            try:
                await session.execute(text("SELECT 1"))
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    await self._call(close)
            return True
        return False

    @staticmethod
    async def _call(function: Any, *args: Any, **kwargs: Any) -> Any:
        result = function(*args, **kwargs)
        return await result if isawaitable(result) else result

    @classmethod
    async def _ping(cls, resource: Any) -> bool:
        if resource is None:
            return False
        ping = getattr(resource, "ping", None)
        if not callable(ping):
            return True
        result = await cls._call(ping)
        return True if result is None else bool(result)

    def _validate_lock_capabilities(self) -> None:
        replicas = getattr(self.settings, "deploy_replicas", 1)
        if isinstance(self.settings, dict):
            replicas = self.settings.get("deploy_replicas", 1)
        if int(replicas) <= 1:
            return
        backend = self.lock_backend
        capabilities = getattr(backend, "capabilities", None)
        if backend is None or not bool(getattr(capabilities, "distributed", False)):
            raise LockBackendUnavailable(
                "multi-replica deployment requires a distributed lock backend"
            )

    # -------------------------------------------------------------- 请求上下文
    @overload
    def for_request(self, request: Envelope[TRequest]) -> RequestContext[TRequest]:
        raise NotImplementedError

    @overload
    def for_request(self, request: Metadata) -> RequestContext[Any]:
        raise NotImplementedError

    def for_request(
        self, request: Envelope[TRequest] | Metadata
    ) -> RequestContext[TRequest] | RequestContext[Any]:
        """Create a request context from an envelope or legacy standalone metadata."""
        if isinstance(request, Envelope):
            envelope = request
            metadata = request.metadata
        elif isinstance(request, Metadata):
            envelope = None
            metadata = request
        else:
            raise TypeError("request must be an Envelope or Metadata")
        deadline = None
        if self.request_timeout_seconds > 0:
            deadline = time.monotonic() + self.request_timeout_seconds
        return RequestContext(
            sysctx=self,
            envelope=envelope,
            _metadata=metadata,
            lock_owner=f"{self.instance_id}:{uuid4().hex}",
            logger=self.logger,
            deadline=deadline,
        )

    # -------------------------------------------------------------- 事务
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Yield one SQLAlchemy session and commit or roll it back on exit.

        Request-level ``db_*`` operations remain independent DBHandler calls and
        do not use the session yielded here.
        """
        db = self.require_db()
        sf = getattr(db, "session_factory", None)
        if not callable(sf):
            raise FrameworkError("db has no session_factory; transaction() unavailable")
        session: AsyncSession = sf()
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        finally:
            await session.close()

    # -------------------------------------------------------------- 生产构造
    @classmethod
    def from_settings(
        cls,
        *,
        redis_url: str | None = None,
        settings: Any = None,
        db: Any = None,
        redis: Any = None,
        etcd_client: Any = None,
        lock_backend: Any = None,
        cache_backend: Any = None,
        table_definitions: Iterable[Any] | None = None,
        instance_id: str | None = None,
        key_prefix: str | None = None,
    ) -> "SystemContext":
        """生产便捷构造：redis 连接串与键前缀默认取自 ``ServiceConfig``（环境变量）。

        - ``OPENJIUWEN_SERVICE_REDIS_URL``（默认 redis://localhost:6379/0）
        - ``OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX``（默认 service）
        连接在 ``start()`` 时才真正建立（``from_url`` 惰性）。
        """
        from ..bootstrap import build_system_context, coerce_config

        cfg = coerce_config(settings)
        updates: dict[str, Any] = {}
        if redis_url is not None:
            updates["redis_url"] = redis_url
        if key_prefix is not None:
            updates["key_prefix"] = key_prefix
        if updates:
            cfg = replace(cfg, **updates)
        return build_system_context(
            cfg,
            db=db,
            redis=redis,
            etcd_client=etcd_client,
            lock_backend=lock_backend,
            cache_backend=cache_backend,
            table_definitions=table_definitions,
            instance_id=instance_id,
        )


__all__ = ["RequestContext", "SystemContext"]
