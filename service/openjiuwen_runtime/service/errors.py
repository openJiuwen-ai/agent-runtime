# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""错误模型（设计 §12）。

统一错误码 + FrameworkError 体系：中间件外层捕获后归一化为
``ResponseEnvelope(ok=False, error_code, error_message)``，绝不裸 500。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class ErrorCode:
    """错误码常量（字符串，便于序列化到 ResponseEnvelope.error_code）。"""

    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    IDEMPOTENT = "idempotent"
    TIMEOUT = "timeout"
    LOCKED = "locked"
    INTERNAL = "internal"
    INTERRUPTED = "interrupted"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    DATABASE_UNAVAILABLE = "database_unavailable"
    REDIS_UNAVAILABLE = "redis_unavailable"
    CACHE_UNAVAILABLE = "cache_unavailable"
    LOCK_BACKEND_UNAVAILABLE = "lock_backend_unavailable"
    KUBERNETES_UNAVAILABLE = "kubernetes_unavailable"
    FORBIDDEN = "forbidden"


class FrameworkError(Exception):
    """框架统一异常基类。``code`` 为错误码，默认 internal。"""

    code: str = ErrorCode.INTERNAL

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    @property
    def message(self) -> str:
        return self.args[0] if self.args else ""


class ValidationError(FrameworkError):
    """请求校验失败。"""

    code = ErrorCode.VALIDATION


class NotFoundError(FrameworkError):
    """资源/路由未找到。"""

    code = ErrorCode.NOT_FOUND


class IdempotentConflict(FrameworkError):
    """幂等冲突：重复 request_id 且 mode=reject。"""

    code = ErrorCode.IDEMPOTENT


class LockNotAcquired(FrameworkError):
    """非阻塞抢锁失败（timeout=0）或等待超时抢不到。"""

    code = ErrorCode.LOCKED


class LockAcquireTimeout(LockNotAcquired):
    """等待锁达到 ``wait_timeout``。"""


class LockLost(FrameworkError):
    """持锁期间续期失锁（被别人抢占或过期）。"""

    code = ErrorCode.LOCKED


class LockBackendUnavailable(FrameworkError):
    """锁后端未配置或不具备请求声明的能力。"""

    code = ErrorCode.LOCK_BACKEND_UNAVAILABLE


class InvalidLockLease(LockLost):
    """租约已失效、释放或凭证与后端状态不匹配。"""


class FrameworkTimeout(FrameworkError):
    """handler 超时。"""

    code = ErrorCode.TIMEOUT


class Interrupted(FrameworkError):
    """Request processing was interrupted explicitly."""

    code = ErrorCode.INTERRUPTED


class DeadlineExceeded(FrameworkError):
    """The absolute request deadline has elapsed."""

    code = ErrorCode.DEADLINE_EXCEEDED


class DatabaseUnavailable(FrameworkError):
    """The request requires a database handler that is not configured."""

    code = ErrorCode.DATABASE_UNAVAILABLE


class RedisUnavailable(FrameworkError):
    """The request requires a Redis client that is not configured."""

    code = ErrorCode.REDIS_UNAVAILABLE


class CacheUnavailable(FrameworkError):
    """The cache is absent, closed, unreachable, or contains invalid data."""

    code = ErrorCode.CACHE_UNAVAILABLE


class KubernetesUnavailable(FrameworkError):
    """Kubernetes operations are absent, closed, or unreachable."""

    code = ErrorCode.KUBERNETES_UNAVAILABLE


class PermissionDenied(FrameworkError):
    """The caller or service identity lacks permission for an operation."""

    code = ErrorCode.FORBIDDEN


@runtime_checkable
class _HasCode(Protocol):
    code: str


def exception_code(exc: BaseException) -> str:
    """归一化任意异常为错误码：FrameworkError 取其 code，其余一律 internal。"""
    if isinstance(exc, FrameworkError):
        return exc.code
    return ErrorCode.INTERNAL


_HTTP_STATUS = {
    ErrorCode.VALIDATION: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.IDEMPOTENT: 409,
    ErrorCode.LOCKED: 423,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.INTERRUPTED: 499,
    ErrorCode.DEADLINE_EXCEEDED: 504,
    ErrorCode.DATABASE_UNAVAILABLE: 503,
    ErrorCode.REDIS_UNAVAILABLE: 503,
    ErrorCode.CACHE_UNAVAILABLE: 503,
    ErrorCode.LOCK_BACKEND_UNAVAILABLE: 503,
    ErrorCode.KUBERNETES_UNAVAILABLE: 503,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.INTERNAL: 500,
}


def http_status_for(code: str) -> int:
    """错误码 → HTTP 状态码；未知码 fail-safe 返回 500。"""
    return _HTTP_STATUS.get(code, 500)
