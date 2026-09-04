"""通用工具：UTC 时间、ID、字符串处理。"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

# user_id / group_id：仅英文字母、数字、下划线、连字符（长度与表定义一致）。
IDENTITY_ID_MAX_LENGTH = 64
IDENTITY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
IDENTITY_ID_PATTERN_STR = r"^[A-Za-z0-9_-]+$"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_datetime(dt: datetime | None) -> str | None:
    """datetime → ISO-8601 UTC 字符串（末尾 Z）；None 返回 None。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def new_uuid4() -> str:
    return str(uuid.uuid4())


def strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_identity_id(value: str, *, field: str = "id") -> str:
    """校验并返回规范化后的 user_id / group_id。"""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} is required")
    if len(cleaned) > IDENTITY_ID_MAX_LENGTH:
        raise ValueError(f"{field} must be at most {IDENTITY_ID_MAX_LENGTH} characters")
    if not IDENTITY_ID_PATTERN.fullmatch(cleaned):
        raise ValueError(
            f"{field} may only contain letters, digits, underscore and hyphen"
        )
    return cleaned
