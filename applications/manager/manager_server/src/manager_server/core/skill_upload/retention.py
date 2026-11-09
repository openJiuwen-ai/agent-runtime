"""企业版 Skill 上传审批：包保留期。

与 AgentServer 共用同一环境变量，两侧必须配置一致。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from manager_server.infrastructure.logger import get_logger

RETENTION_DAYS_ENV = "SKILL_UPLOAD_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 7
_MIN_RETENTION_DAYS = 1

_log = get_logger(__name__)


def retention_days() -> int:
    """保留天数：默认 7 天，小于 1 或非法值回落默认值，不设上限。"""
    raw = os.getenv(RETENTION_DAYS_ENV, "").strip()
    if not raw:
        return DEFAULT_RETENTION_DAYS
    try:
        value = int(raw)
    except ValueError:
        _log.warning("invalid_retention_days", value=raw)
        return DEFAULT_RETENTION_DAYS
    if value < _MIN_RETENTION_DAYS:
        _log.warning("retention_days_below_minimum", value=raw)
        return DEFAULT_RETENTION_DAYS
    return value


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def package_expires_at(value: Any, *, now: datetime | None = None) -> datetime:
    """``min(now, staged_at) + TTL``：客户端只能让单据更早失效。"""
    base = now or datetime.now(timezone.utc)
    staged_at = _parse_time(value)
    if staged_at is not None and staged_at < base:
        base = staged_at
    return base + timedelta(days=retention_days())
