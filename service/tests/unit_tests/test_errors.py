# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""错误模型：错误码、异常类、code 归一化与 HTTP 状态映射（设计 §12）。"""

import pytest

from openjiuwen_runtime.service.errors import (
    CacheUnavailable,
    DatabaseUnavailable,
    DeadlineExceeded,
    ErrorCode,
    FrameworkError,
    IdempotentConflict,
    Interrupted,
    KubernetesUnavailable,
    LockLost,
    LockNotAcquired,
    NotFoundError,
    PermissionDenied,
    RedisUnavailable,
    ValidationError,
    exception_code,
    http_status_for,
)


@pytest.mark.unit
def test_framework_error_default_code():
    err = FrameworkError("boom")
    assert err.code == ErrorCode.INTERNAL
    assert str(err) == "boom"
    assert isinstance(err, Exception)


@pytest.mark.unit
def test_subclass_codes():
    assert ValidationError("x").code == ErrorCode.VALIDATION
    assert NotFoundError("x").code == ErrorCode.NOT_FOUND
    assert IdempotentConflict("x").code == ErrorCode.IDEMPOTENT
    assert LockNotAcquired("x").code == ErrorCode.LOCKED
    assert LockLost("x").code == ErrorCode.LOCKED
    assert Interrupted("x").code == ErrorCode.INTERRUPTED
    assert DeadlineExceeded("x").code == ErrorCode.DEADLINE_EXCEEDED
    assert DatabaseUnavailable("x").code == ErrorCode.DATABASE_UNAVAILABLE
    assert RedisUnavailable("x").code == ErrorCode.REDIS_UNAVAILABLE
    assert CacheUnavailable("x").code == ErrorCode.CACHE_UNAVAILABLE
    assert KubernetesUnavailable("x").code == ErrorCode.KUBERNETES_UNAVAILABLE
    assert PermissionDenied("x").code == ErrorCode.FORBIDDEN
    # 都是 FrameworkError 子类 → 中间件可统一捕获
    for exc in (
        ValidationError(""),
        NotFoundError(""),
        IdempotentConflict(""),
        LockNotAcquired(""),
        LockLost(""),
    ):
        assert isinstance(exc, FrameworkError)


@pytest.mark.unit
def test_explicit_code_override():
    # 允许实例级覆盖 code
    err = FrameworkError("x", code=ErrorCode.CONFLICT)
    assert err.code == ErrorCode.CONFLICT


@pytest.mark.unit
def test_exception_code_normalizes_unknown():
    assert exception_code(ValidationError("x")) == ErrorCode.VALIDATION
    # 非 FrameworkError 一律归一为 internal
    assert exception_code(ValueError("x")) == ErrorCode.INTERNAL
    assert exception_code(RuntimeError()) == ErrorCode.INTERNAL


@pytest.mark.unit
def test_http_status_mapping():
    assert http_status_for(ErrorCode.VALIDATION) == 400
    assert http_status_for(ErrorCode.NOT_FOUND) == 404
    assert http_status_for(ErrorCode.CONFLICT) == 409
    assert http_status_for(ErrorCode.IDEMPOTENT) == 409
    assert http_status_for(ErrorCode.LOCKED) == 423
    assert http_status_for(ErrorCode.TIMEOUT) == 504
    assert http_status_for(ErrorCode.INTERRUPTED) == 499
    assert http_status_for(ErrorCode.DEADLINE_EXCEEDED) == 504
    assert http_status_for(ErrorCode.DATABASE_UNAVAILABLE) == 503
    assert http_status_for(ErrorCode.REDIS_UNAVAILABLE) == 503
    assert http_status_for(ErrorCode.CACHE_UNAVAILABLE) == 503
    assert http_status_for(ErrorCode.KUBERNETES_UNAVAILABLE) == 503
    assert http_status_for(ErrorCode.FORBIDDEN) == 403
    assert http_status_for(ErrorCode.INTERNAL) == 500
    # 未知 code → 500（fail-safe）
    assert http_status_for("totally-unknown") == 500
