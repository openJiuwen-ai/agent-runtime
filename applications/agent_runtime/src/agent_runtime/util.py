# coding: utf-8
"""agent-runtime 公共小工具（时间 / scope 派生 / bytes 解码）。"""

from __future__ import annotations

import hashlib
import time
from typing import Any


def now_ts() -> int:
    """当前 Unix 时间戳（秒）。Redis 键里的时间统一用秒级 int。"""
    return int(time.time())


def scope_id_of(group_id: str, bot_id: str) -> str:
    """scope_id = md5(group_id + '\\x00' + bot_id)。

    \\x00 分隔符防撞号：否则 (ab,c) 与 (a,bc) 会派生出同一 scope（HLD §7.5）。
    """
    return hashlib.md5(f"{group_id}\x00{bot_id}".encode()).hexdigest()


def fingerprint(fields: dict[str, Any]) -> str:
    """字典字段的确定性 hash 指纹（deploy_ver 用）：按 key 排序后 md5。"""
    canonical = ",".join(
        f"{k}={fields[k]!r}" for k in sorted(fields) if fields[k] is not None
    )
    return hashlib.md5(canonical.encode()).hexdigest()[:16]


def s(value: Any) -> str:
    """Redis 返回值统一转 str（真实 client 是 bytes，fakeredis 可能是 str）。"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def to_int(value: Any, default: int = 0) -> int:
    """Redis 数值字段安全转 int（None/空串/异常回退 default）。"""
    try:
        return int(s(value))
    except (TypeError, ValueError):
        return default
