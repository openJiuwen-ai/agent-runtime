# coding: utf-8
"""agent-runtime 公共小工具（时间 / 指纹 / bytes 解码）。

scope_id 不再在此派生——由 config_sync 全量下发（见 session_manager/routing.py）。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any


def now_ts() -> int:
    """当前 Unix 时间戳（秒）。Redis 键里的时间统一用秒级 int。"""
    return int(time.time())


def utc_now() -> datetime:
    """当前 UTC 时间（naive）。与 DB ``datetime`` 列、过期判定统一用此形态。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc_naive(value: datetime) -> datetime:
    """任意 datetime → naive UTC（已 naive 则原样返回）。"""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_datetime(value: Any, *, field: str = "datetime") -> datetime | None:
    """wire/DB 时间值 → naive UTC datetime。

    ``None`` / 空串 → ``None``；``datetime`` / ISO-8601 字符串可解析；
    其他类型或非法串 → ``ValueError``（调用方按需映射为业务错误）。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return as_utc_naive(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"{field} must be an ISO-8601 datetime, got {value!r}"
            ) from exc
        return as_utc_naive(parsed)
    raise ValueError(
        f"{field} must be an ISO-8601 datetime or null, got {value!r}"
    )


def key_unsafe(value: Any) -> bool:
    """标识符是否含 ``{``/``}``——不可进入 Redis 键名。

    键前缀的 hash tag（如 ``{session_manager}:``）靠**第一对**花括号定槽：
    外部标识符（scope_id/session_id 等）若自带 ``}``，会提前截断 tag、把
    该标识符的键甩到别的 slot，Redis Cluster 下多键 Lua 直接跨槽报错。
    入口处（orchestrator.route/touch、config_store 行解析）据此拒绝。
    """
    text = s(value)
    return "{" in text or "}" in text


def fingerprint(fields: dict[str, Any]) -> str:
    """字典字段的确定性 hash 指纹（deploy_ver 用）：过滤 None 后按键序规范化
    JSON 序列化再 md5。

    **嵌套 dict（如 agent_env）必须键序无关**——DB JSON 列回读会重排键序，
    repr(dict) 对键序敏感会导致同一模板算出不同 deploy_ver（2026-08-26 真环境
    实测：SM 传入的 want_ver 与池内 Pod 版本永不相等，暖 Pod 复用被跳过）。
    """
    canonical = json.dumps(
        {k: fields[k] for k in fields if fields[k] is not None},
        sort_keys=True, ensure_ascii=False, default=str,
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
