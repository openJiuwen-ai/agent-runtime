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
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, TypeVar, overload
from uuid import uuid4

import redis.asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ServiceConfig
from ..envelope import Envelope, Metadata
from ..errors import DatabaseUnavailable, FrameworkError, RedisUnavailable
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
        request_timeout_seconds: float | None = None,
        _owns_redis: bool = False,
    ) -> None:
        self.redis = redis
        self.db = db
        self.settings = settings
        if request_timeout_seconds is None:
            request_timeout_seconds = self._request_timeout_from_settings(settings)
        if not math.isfinite(request_timeout_seconds) or request_timeout_seconds < 0:
            raise ValueError("request_timeout_seconds must be a finite non-negative number")
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.key_prefix = key_prefix or ""
        self.instance_id = instance_id or f"{socket.gethostname()}:{uuid4().hex[:8]}"
        self.logger = logger or _logger
        if audit_logger is not None and audit is not None:
            raise ValueError("audit_logger and audit cannot both be provided")
        self.audit_logger: AuditLogger = (
            audit_logger or audit or LoggingAuditLogger(self.logger)
        )
        self._owns_redis = _owns_redis
        self._started = False

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
        if self.db is not None and hasattr(self.db, "connect"):
            await self.db.connect()
        if self.redis is not None:
            await self.redis.ping()  # fakeredis / 真 redis 均支持，失败即早上报
        self._started = True

    async def stop(self) -> None:
        if self.redis is not None and self._owns_redis:
            await self.redis.aclose()
        if self.db is not None and hasattr(self.db, "disconnect"):
            await self.db.disconnect()
        self._started = False

    # -------------------------------------------------------------- 请求上下文
    @overload
    def for_request(self, request: Envelope[TRequest]) -> RequestContext[TRequest]:
        ...

    @overload
    def for_request(self, request: Metadata) -> RequestContext[Any]:
        ...

    def for_request(self, request: Envelope[TRequest] | Metadata) -> RequestContext[TRequest] | RequestContext[Any]:
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
        key_prefix: str | None = None,
    ) -> "SystemContext":
        """生产便捷构造：redis 连接串与键前缀默认取自 ``ServiceConfig``（环境变量）。

        - ``OPENJIUWEN_SERVICE_REDIS_URL``（默认 redis://localhost:6379/0）
        - ``OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX``（默认 service）
        连接在 ``start()`` 时才真正建立（``from_url`` 惰性）。
        """
        cfg = ServiceConfig.from_env()
        url = cfg.redis_url if redis_url is None else redis_url
        kp = cfg.key_prefix if key_prefix is None else key_prefix
        client = redis.asyncio.from_url(url, decode_responses=False)
        return cls(
            redis=client,
            db=db,
            settings=cfg if settings is None else settings,
            key_prefix=kp,
            _owns_redis=True,
        )


__all__ = ["RequestContext", "SystemContext"]
