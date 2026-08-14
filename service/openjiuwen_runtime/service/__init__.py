# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""通用 Python 分布式服务框架（openjiuwen_runtime.service）。

对外出口随子模块实现逐步充实（Envelope / App / SystemContext / 原语 / 错误类）。
"""

from .envelope import Envelope, Metadata, ResponseEnvelope, StreamChunk
from .config import ServiceConfig
from .errors import (
    CacheUnavailable,
    DatabaseUnavailable,
    DeadlineExceeded,
    ErrorCode,
    FrameworkError,
    IdempotentConflict,
    InvalidLockLease,
    LockAcquireTimeout,
    LockBackendUnavailable,
    LockLost,
    LockNotAcquired,
    NotFoundError,
    Interrupted,
    KubernetesUnavailable,
    PermissionDenied,
    RedisUnavailable,
    ValidationError,
)
from .context import (
    AuditEvent,
    AuditLogger,
    BaseCacheBackend,
    Cache,
    CacheBackend,
    CacheBackendFactory,
    CacheMetrics,
    CacheSerializer,
    EtcdLockBackend,
    LoggingAuditLogger,
    LeaseState,
    LockBackend,
    LockBackendFactory,
    LockCapabilities,
    LockCredential,
    LockLease,
    LockManager,
    JsonCacheSerializer,
    FakeKubernetesOperations,
    KubernetesAsyncioOperations,
    KubernetesOperations,
    MemoryCacheBackend,
    MemoryLockBackend,
    NoopAuditLogger,
    RedisLockBackend,
    RedisCacheBackend,
    RequestContext,
    PodCreateSpec,
    PodDeleteResult,
    PodSummary,
    SystemContext,
    TypedAppContext,
    build_lock_backend,
    build_cache_backend,
    create_cache_backend,
    create_lock_backend,
)
from .bootstrap import (
    bootstrap_system,
    build_db_handler,
    build_redis_client,
    build_redis_handler,
    build_system_context,
    create_system_context,
    shutdown_system,
)
from .context.primitives.idempotency import idempotency_guard
from .routing.handlers import (
    FunctionMessageHandler,
    FunctionStreamMessageHandler,
    HandlerModule,
    HandlerRegistry,
    HandlerSpec,
    MessageHandler,
    StreamMessageHandler,
)
from .security import OAuth2AccessControl
from .server.app import App

__version__ = "0.1.0"

__all__ = [
    # envelope
    "Envelope",
    "Metadata",
    "ResponseEnvelope",
    "StreamChunk",
    # config
    "ServiceConfig",
    # errors
    "ErrorCode",
    "FrameworkError",
    "Interrupted",
    "DeadlineExceeded",
    "DatabaseUnavailable",
    "RedisUnavailable",
    "CacheUnavailable",
    "KubernetesUnavailable",
    "PermissionDenied",
    "ValidationError",
    "NotFoundError",
    "IdempotentConflict",
    "LockAcquireTimeout",
    "LockBackendUnavailable",
    "InvalidLockLease",
    "LockNotAcquired",
    "LockLost",
    # context
    "SystemContext",
    "RequestContext",
    "TypedAppContext",
    "AuditEvent",
    "AuditLogger",
    "BaseCacheBackend",
    "Cache",
    "CacheBackend",
    "CacheBackendFactory",
    "CacheMetrics",
    "CacheSerializer",
    "EtcdLockBackend",
    "LoggingAuditLogger",
    "NoopAuditLogger",
    "FakeKubernetesOperations",
    "KubernetesAsyncioOperations",
    "KubernetesOperations",
    "PodCreateSpec",
    "PodDeleteResult",
    "PodSummary",
    # locks
    "LeaseState",
    "LockBackend",
    "LockBackendFactory",
    "LockCapabilities",
    "LockCredential",
    "LockLease",
    "LockManager",
    "JsonCacheSerializer",
    "MemoryCacheBackend",
    "MemoryLockBackend",
    "RedisLockBackend",
    "RedisCacheBackend",
    "build_cache_backend",
    "build_lock_backend",
    "create_cache_backend",
    "create_lock_backend",
    # bootstrap
    "bootstrap_system",
    "build_db_handler",
    "build_redis_client",
    "build_redis_handler",
    "build_system_context",
    "create_system_context",
    "shutdown_system",
    # handlers
    "HandlerSpec",
    "MessageHandler",
    "StreamMessageHandler",
    "FunctionMessageHandler",
    "FunctionStreamMessageHandler",
    "HandlerRegistry",
    "HandlerModule",
    # middleware
    "idempotency_guard",
    # security
    "OAuth2AccessControl",
    # server
    "App",
]
