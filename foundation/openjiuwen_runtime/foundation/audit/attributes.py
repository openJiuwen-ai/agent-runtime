# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""按 format 字段清单组装 OTEL LogRecord attributes。"""

from __future__ import annotations

import inspect
import logging
import os
import threading
from typing import Any, Mapping

from .clock import LocalClock, format_timestamp
from .constants import (
    ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT,
    EVENT_TYPE_ATTR,
    KEYWORD_EVT,
    KEYWORD_UA,
    PLACEHOLDER_DEFAULT,
)
from .context import get_audit_context
from .models import AuditSnapshot

logger = logging.getLogger(__name__)

_AUDIT_MODULE_PREFIX = "openjiuwen_runtime.foundation.audit"


def capture_caller(*, skip_modules: tuple[str, ...] = (_AUDIT_MODULE_PREFIX,)) -> str:
    """栈回溯：模块.函数:行号（跳过本包帧）。"""
    frame = inspect.currentframe()
    try:
        while frame is not None:
            module = frame.f_globals.get("__name__", "")
            if any(module.startswith(prefix) for prefix in skip_modules):
                frame = frame.f_back
                continue
            func = frame.f_code.co_name
            line = frame.f_lineno
            short = module.rsplit(".", 1)[-1] if module else "<unknown>"
            return f"{short}.{func}:{line}"
        return PLACEHOLDER_DEFAULT
    finally:
        del frame


def _stringify(value: Any, placeholder: str) -> str:
    if value is None:
        return placeholder
    text = str(value)
    if text == "":
        return placeholder
    return text


def _truncate(value: str, max_len: int) -> str:
    if max_len <= 0 or len(value) <= max_len:
        return value
    return value[:max_len]


def _tid_string() -> str:
    ident = threading.get_ident()
    return f"tid-{ident:x}"


def build_audit_attributes(
    snapshot: AuditSnapshot,
    *,
    event_type: str,
    level: str,
    fields: Mapping[str, Any] | None = None,
    clock: LocalClock | None = None,
    caller: str | None = None,
) -> dict[str, str]:
    """合并配置 / 自动字段 / ContextVar / 显式参数，再按 format 过滤。

    优先级：显式 fields > ContextVar > 自动 / 配置。
    CUSTID 恒为 placeholder（本阶段固定 ``-`` 语义由 placeholder 表达）。
    UID：仅显式传入（含 ``system``）或 ContextVar user_id；否则 placeholder。
    """
    fmt = snapshot.format
    placeholder = fmt.placeholder or PLACEHOLDER_DEFAULT
    max_len = snapshot.attribute_value_max_length or ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT
    explicit = dict(fields or {})
    ctx = get_audit_context()
    time_source = clock or LocalClock()

    merged: dict[str, Any] = {}

    # 配置注入
    merged["schema_version"] = fmt.schema_version
    merged["data_center"] = snapshot.identity.data_center
    merged["system_code"] = snapshot.identity.system_code
    merged["node"] = snapshot.identity.node

    # 自动
    merged["timestamp"] = format_timestamp(time_source.now(), fmt.timestamp_format)
    merged["level"] = level
    merged["pid"] = str(os.getpid())
    merged["tid"] = _tid_string()
    merged["caller"] = caller if caller is not None else capture_caller()

    # ContextVar
    session_id = ctx.get("session_id")
    request_id = ctx.get("request_id")
    user_id = ctx.get("user_id")
    src_ip = ctx.get("src_ip")
    dst_ip = ctx.get("dst_ip")
    if session_id is not None and str(session_id) != "":
        merged["trace_id"] = session_id
    if request_id is not None and str(request_id) != "":
        merged["txn_seq"] = request_id
    if user_id is not None and str(user_id) != "":
        merged["UID"] = user_id
    if src_ip is not None and str(src_ip) != "":
        merged["SRCIP"] = src_ip
    if dst_ip is not None and str(dst_ip) != "":
        merged["DSTIP"] = dst_ip

    # 显式覆盖（含 UID=system）；CUSTID 本阶段恒为 placeholder，不允许覆盖
    for key, value in explicit.items():
        if key == EVENT_TYPE_ATTR or key == "CUSTID":
            continue
        merged[key] = value
    merged["CUSTID"] = placeholder

    # 按 format 过滤并占位
    attributes: dict[str, str] = {}
    for name in fmt.enabled_fields:
        attributes[name] = _truncate(
            _stringify(merged.get(name), placeholder),
            max_len,
        )

    # event_type 始终写入
    attributes[EVENT_TYPE_ATTR] = event_type

    _warn_missing_required(fmt.required_fields, attributes, event_type, placeholder)
    return attributes


# 本阶段常合法为 placeholder，不因占位刷 warning。
_PLACEHOLDER_OK_REQUIRED = frozenset(
    {"UID", "CUSTID", "SRCIP", "DSTIP", "timestamp", "level"}
)


def _warn_missing_required(
    required_fields: tuple[str, ...],
    attributes: Mapping[str, str],
    event_type: str,
    placeholder: str,
) -> None:
    """required 仍缺或业务关键字段仍为占位时 warning，但仍由调用方继续 emit。"""
    missing: list[str] = []

    for name in required_fields:
        if name not in attributes:
            missing.append(name)

    if event_type == KEYWORD_UA:
        if attributes.get("UA", placeholder) == placeholder:
            missing.append("UA")
    elif event_type == KEYWORD_EVT:
        if attributes.get("EVT", placeholder) == placeholder:
            missing.append("EVT")

    for name in required_fields:
        if name in _PLACEHOLDER_OK_REQUIRED or name in ("UA", "EVT"):
            continue
        if attributes.get(name, placeholder) == placeholder:
            missing.append(name)

    if not missing:
        return

    seen: set[str] = set()
    ordered: list[str] = []
    for name in missing:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    logger.warning(
        "audit required fields missing or placeholder: %s (event_type=%s)",
        ", ".join(ordered),
        event_type,
    )
