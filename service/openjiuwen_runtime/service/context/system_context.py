# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SystemContext / RequestContext（设计 §8）。

- 进程级 SystemContext（lifespan 创建/释放）：db / redis / settings / logger / 原语工厂。
- 请求级 RequestContext（``for_request(metadata)`` 派生）：request_id 等 + lock_owner +
  绑定 request_id 的 logger + 对进程级组件的引用；handler 只通过它访问能力。
- 事务：``async with ctx.transaction() as s`` 取 SQLAlchemy session（多操作原子）。
- 硬约束：handler 禁止读写模块级可变状态——无内存状态的多副本。
"""
from __future__ import annotations

import logging
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

import redis.asyncio

from ..config import ServiceConfig
from ..envelope import Metadata
from ..errors import FrameworkError
from .primitives.kv_store import KVStore

_logger = logging.getLogger("openjiuwen_runtime.service")


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
        _owns_redis: bool = False,
    ) -> None:
        self.redis = redis
        self.db = db
        self.settings = settings
        self.key_prefix = key_prefix or ""
        self.instance_id = instance_id or f"{socket.gethostname()}:{uuid4().hex[:8]}"
        self._owns_redis = _owns_redis
        self._started = False

    # -------------------------------------------------------------- 命名空间
    def _ns(self, suffix: str) -> str:
        return f"{self.key_prefix}:{suffix}" if self.key_prefix else suffix

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
    def for_request(self, metadata: Metadata) -> "RequestContext":
        return RequestContext(
            sysctx=self,
            request_id=metadata.request_id,
            user_id=metadata.user_id,
            chat_id=metadata.chat_id,
            session_id=metadata.session_id,
            trace_id=metadata.trace_id,
            bot_id=metadata.bot_id,
            channel=metadata.channel,
            lock_owner=f"{self.instance_id}:{uuid4().hex}",
            logger=_logger,
        )

    # -------------------------------------------------------------- 事务
    @asynccontextmanager
    async def transaction(self):
        """多操作原子事务（独立 SQLAlchemy session，不改 foundation）。

        ``db.session_factory`` 在 foundation ``SQLAlchemyHandler`` 上已暴露；未连接时为 None。
        """
        sf = getattr(self.db, "session_factory", None) if self.db is not None else None
        if sf is None:
            raise FrameworkError("db has no session_factory; transaction() unavailable")
        session = sf()
        try:
            yield session
            await session.commit()
        except Exception:
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
        return cls(redis=client, db=db, settings=settings,
                   key_prefix=kp, _owns_redis=True)


@dataclass
class RequestContext:
    """每条 Envelope 派生的请求级上下文；handler 经它访问所有能力。"""

    sysctx: SystemContext
    request_id: str
    user_id: Optional[str] = None
    chat_id: Optional[str] = None
    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    bot_id: Optional[str] = None
    channel: Optional[str] = None
    lock_owner: str = ""
    logger: logging.Logger = field(default_factory=lambda: _logger)
    _kv: Optional[KVStore] = field(default=None, repr=False, compare=False)
    _idem: Any = field(default=None, repr=False, compare=False)
    _queue: Any = field(default=None, repr=False, compare=False)
    _pubsub: Any = field(default=None, repr=False, compare=False)

    @property
    def db(self) -> Any:
        return self.sysctx.db

    @property
    def kv(self) -> KVStore:
        """分布式字典 / 会话存储（顶替进程内 dict）。"""
        if self._kv is None:
            self._kv = KVStore(self.sysctx.redis, prefix=self.sysctx._ns("kv"))
        return self._kv

    @property
    def idempotency(self):
        """幂等：按 request_id 全局去重 / 结果回放。"""
        if self._idem is None:
            from .primitives.idempotency import Idempotency
            self._idem = Idempotency(self.sysctx.redis, prefix=self.sysctx._ns("idem"))
        return self._idem

    @property
    def queue(self):
        """队列：跨副本有序、副本重启不丢（Redis Streams + 消费组）。"""
        if self._queue is None:
            from .primitives.stream_queue import StreamQueue
            self._queue = StreamQueue(self.sysctx.redis, prefix=self.sysctx._ns("queue"))
        return self._queue

    @property
    def pubsub(self):
        """发布订阅：瞬时扇出（Redis Pub/Sub）。"""
        if self._pubsub is None:
            from .primitives.pubsub import PubSub
            self._pubsub = PubSub(self.sysctx.redis, prefix=self.sysctx._ns("pubsub"))
        return self._pubsub

    def lock(self, key: str, *, ttl: float = 30, timeout: float = 0, renew_interval: float | None = None):
        """分布式锁：``async with ctx.lock(key, ttl=..., timeout=...)``。"""
        from .primitives.lock import DistributedLock
        return DistributedLock(
            self.sysctx.redis, key, owner=self.lock_owner, ttl=ttl, timeout=timeout,
            prefix=self.sysctx._ns("lock"), renew_interval=renew_interval)

    def transaction(self):
        """多操作原子事务（委托 SystemContext）。"""
        return self.sysctx.transaction()
