# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Manager 侧执行护栏 body 校验，规则与 Gateway 副本一致。"""

from __future__ import annotations

import copy

import pytest

from manager_server.schemas.execution_guard_body import (
    DEFAULT_EXECUTION_GUARD_BODY,
    validate_execution_guard_body,
)


def test_default_body_is_accepted() -> None:
    assert validate_execution_guard_body(DEFAULT_EXECUTION_GUARD_BODY) == (
        DEFAULT_EXECUTION_GUARD_BODY
    )


def test_missing_field_is_rejected() -> None:
    body = copy.deepcopy(DEFAULT_EXECUTION_GUARD_BODY)
    del body["model_anomaly_detection_rail"]["transient_max_retries"]
    with pytest.raises(ValueError, match="missing fields: transient_max_retries"):
        validate_execution_guard_body(body)


def test_repeat_window_too_small_is_rejected() -> None:
    body = copy.deepcopy(DEFAULT_EXECUTION_GUARD_BODY)
    body["model_anomaly_detection_rail"]["repeat_window_chars"] = 10
    with pytest.raises(ValueError, match="repeat_window_chars must be >= 384"):
        validate_execution_guard_body(body)


def test_critical_below_warning_is_rejected() -> None:
    body = copy.deepcopy(DEFAULT_EXECUTION_GUARD_BODY)
    body["circuit_breaker"]["critical_threshold"] = 5
    with pytest.raises(ValueError, match="critical_threshold must be >= warning_threshold"):
        validate_execution_guard_body(body)


def test_unknown_field_is_rejected() -> None:
    body = copy.deepcopy(DEFAULT_EXECUTION_GUARD_BODY)
    body["model_anomaly_detection_rail"]["backoff_seconds"] = [0.5]
    with pytest.raises(ValueError, match="unknown fields: backoff_seconds"):
        validate_execution_guard_body(body)


def test_int_above_int32_max_is_rejected() -> None:
    body = copy.deepcopy(DEFAULT_EXECUTION_GUARD_BODY)
    body["model_anomaly_detection_rail"]["max_retries"] = 2_147_483_648
    with pytest.raises(ValueError, match="max_retries must be <= 2147483647"):
        validate_execution_guard_body(body)
