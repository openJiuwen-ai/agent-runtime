# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""通用 Python 分布式服务框架（openjiuwen_runtime.service）。

对外出口随子模块实现逐步充实（Envelope / App / SystemContext / 原语 / 错误类）。
"""

from .envelope import Envelope, Metadata, ResponseEnvelope, StreamChunk
from .config import ServiceConfig
from .errors import (
    DatabaseUnavailable,
    DeadlineExceeded,
    ErrorCode,
    FrameworkError,
    IdempotentConflict,
    LockLost,
    LockNotAcquired,
    NotFoundError,
    Interrupted,
    RedisUnavailable,
    ValidationError,
)
from .context import (
    AuditEvent,
    AuditLogger,
    LoggingAuditLogger,
    NoopAuditLogger,
    RequestContext,
    SystemContext,
    TypedAppContext,
)
from .context.periodic import (
    JobRunner,
    SingleLeaderCoordinator,
    TickLock,
    create_single_leader_job,
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
    "ValidationError",
    "NotFoundError",
    "IdempotentConflict",
    "LockNotAcquired",
    "LockLost",
    # context
    "SystemContext",
    "RequestContext",
    "TypedAppContext",
    "AuditEvent",
    "AuditLogger",
    "LoggingAuditLogger",
    "NoopAuditLogger",
    # periodic
    "JobRunner",
    "SingleLeaderCoordinator",
    "TickLock",
    "create_single_leader_job",
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
