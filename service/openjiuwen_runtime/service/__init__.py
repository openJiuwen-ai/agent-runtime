# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""通用 Python 分布式服务框架（openjiuwen_runtime.service）。

对外出口随子模块实现逐步充实（Envelope / App / SystemContext / 原语 / 错误类）。
"""
from .envelope import Envelope, Metadata, ResponseEnvelope, StreamChunk
from .config import ServiceConfig
from .errors import (
    ErrorCode,
    FrameworkError,
    IdempotentConflict,
    LockLost,
    LockNotAcquired,
    NotFoundError,
    ValidationError,
)
from .context.system_context import RequestContext, SystemContext
from .context.primitives.idempotency import idempotency_guard
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
    "ValidationError",
    "NotFoundError",
    "IdempotentConflict",
    "LockNotAcquired",
    "LockLost",
    # context
    "SystemContext",
    "RequestContext",
    # middleware
    "idempotency_guard",
    # server
    "App",
]
