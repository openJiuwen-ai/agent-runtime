# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""manager 包保留期：环境变量解析与审批单期限计算。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from manager_server.core.skill_upload.retention import (
    RETENTION_DAYS_ENV,
    package_expires_at,
    retention_days,
)

pytestmark = pytest.mark.unit


def test_retention_days_defaults_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RETENTION_DAYS_ENV, raising=False)
    assert retention_days() == 7

    monkeypatch.setenv(RETENTION_DAYS_ENV, "3")
    assert retention_days() == 3

    monkeypatch.setenv(RETENTION_DAYS_ENV, "365")
    assert retention_days() == 365

    monkeypatch.setenv(RETENTION_DAYS_ENV, "0")
    assert retention_days() == 7

    monkeypatch.setenv(RETENTION_DAYS_ENV, "not-a-number")
    assert retention_days() == 7


def test_package_expires_at_never_exceeds_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RETENTION_DAYS_ENV, "7")
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=30)
    past = now - timedelta(days=3)

    assert package_expires_at(future, now=now) == now + timedelta(days=7)
    assert package_expires_at(past, now=now) == past + timedelta(days=7)
    assert package_expires_at(None, now=now) == now + timedelta(days=7)
    assert package_expires_at("not-a-time", now=now) == now + timedelta(days=7)
