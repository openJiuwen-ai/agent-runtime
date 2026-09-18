# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OTLP emit — attribute assembly + self-owned LoggerProvider.

按内置默认字段清单组装 attributes，经 OTEL Logs 接口异步 emit
（BatchLogRecordProcessor 批量，不阻塞业务热路径）。
OTLP endpoint / service.name 从部署环境变量读取。

打点 API 二选一：

- :func:`log_event` —— 高层语义：传业务语义（submdl/proc/success），
  UA/EVT、RSPCD、RESULT、文案由 SDK 自动推导；业务挂点日常使用。
- :func:`log_audit` —— 契约形态：event_type 与全部契约字段由调用方
  显式给定，SDK 不做推导；需要逐字段控制时使用。
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from . import context as ctx

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0.0"
AUDIT_SCOPE = "openjiuwen.audit"
_PLACEHOLDER = "-"

_LEVEL_SEVERITY = {"INFO": 9, "WARN": 13, "ERROR": 17, "FATAL": 21}


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def audit_enabled() -> bool:
    """OTEL_ENABLED=true 且 OTLP endpoint 已配置时启用。"""
    return _env("OTEL_ENABLED").lower() == "true" and bool(
        _env("OTEL_EXPORTER_OTLP_ENDPOINT")
    )


def _node() -> str:
    return socket.gethostname()


def _timestamp() -> str:
    now = time.time()
    base = time.strftime("%Y%m%d-%H:%M:%S", time.localtime(now))
    return f"{base}.{int(now * 1000) % 1000:03d}"


_SCOPES_DIR = os.path.dirname(__file__)


def _caller() -> str:
    """First stack frame outside this package: ``module.func:lineno``."""
    frame = traceback.extract_stack()
    for entry in reversed(frame[:-1]):
        if _SCOPES_DIR not in entry.filename:
            return f"{entry.filename.rsplit('/', 1)[-1].removesuffix('.py')}:{entry.name}:{entry.lineno}"
    return _PLACEHOLDER


def _tid() -> str:
    return f"tid-{threading.get_ident():x}"


def _severity(level: str) -> int:
    return _LEVEL_SEVERITY.get(level.upper(), 9)


def build_attributes(
    event_type: str,
    level: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the audit attribute set（默认字段清单）.

    头部自动字段 + 内容要素 + 小写桥接键（供观测前端读取）。
    """
    snap = ctx.snapshot()
    evt = event_type.upper()
    success = evt == "UA"

    def pick(explicit: Any, ctx_key: str, default: str = _PLACEHOLDER) -> str:
        if explicit is not None and str(explicit).strip():
            return str(explicit)
        return snap.get(ctx_key) or default

    attributes: dict[str, Any] = {
        # ---- 头部（SDK 自动补全） ----
        "schema_version": SCHEMA_VERSION,
        "timestamp": _timestamp(),
        "level": level.upper(),
        "data_center": _PLACEHOLDER,
        "system_code": _PLACEHOLDER,
        "node": _node(),
        "pid": str(os.getpid()),
        "tid": _tid(),
        "caller": _caller(),
        # ---- 内容要素 ----
        "event_type": evt,
        "UID": pick(fields.get("UID"), "user_id"),
        "CUSTID": _PLACEHOLDER,
        "SRCIP": pick(fields.get("SRCIP"), "srcip"),
        "DSTIP": pick(fields.get("DSTIP"), "dstip"),
        "RSPCD": fields.get("RSPCD") or _PLACEHOLDER,
        "SUBMDL": fields.get("SUBMDL") or _PLACEHOLDER,
        "PROC": fields.get("PROC") or _PLACEHOLDER,
        "MSG": fields.get("MSG") or _PLACEHOLDER,
        "COST": fields.get("COST", _PLACEHOLDER),
        "SVRNAM": fields.get("SVRNAM", _PLACEHOLDER),
    }
    desc = fields.get(evt)  # 与关键字同名的文案（UA=成功描述 / EVT=违规描述）
    if desc:
        attributes[evt] = desc

    # ---- 沙箱扩展（按需携带） ----
    for key in ("ACTION", "SANDBOXID", "RESULT"):
        if fields.get(key) is not None:
            attributes[key] = fields[key]

    # ---- 桥接键（观测前端依赖的小写别名） ----
    attributes["audit_type"] = evt.lower()
    attributes["submdl"] = attributes["SUBMDL"]
    attributes["proc"] = attributes["PROC"]
    attributes["outcome"] = "success" if success else "fail"
    for key in ("session_id", "request_id", "user_id", "bot_id", "group_id"):
        # 显式传参优先于 ContextVar 快照；UID 为同一身份的大写契约键
        value = fields.get(key) or snap.get(key) or (
            fields.get("UID") if key == "user_id" else None
        )
        if value:
            attributes[key] = str(value)
    extra = fields.get("extra")
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value is not None:
                attributes[key] = value
    return attributes


_provider = None
_provider_lock = threading.Lock()


def _get_logger():
    """Lazily build the audit-only LoggerProvider (never the global one)."""
    global _provider
    if _provider is not None:
        return _provider.get_logger(AUDIT_SCOPE)
    with _provider_lock:
        if _provider is None:
            endpoint = _env("OTEL_EXPORTER_OTLP_ENDPOINT").rstrip("/")
            exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs")
            resource = Resource.create(
                {
                    "service.name": _env("OTEL_SERVICE_NAME")
                    or "openjiuwen-runtime",
                }
            )
            provider = LoggerProvider(resource=resource)
            provider.add_log_record_processor(
                BatchLogRecordProcessor(exporter)
            )
            _provider = provider
        return _provider.get_logger(AUDIT_SCOPE)


def emit(event_type: str, level: str, fields: dict[str, Any]) -> None:
    """Assemble + emit one audit record. Never raises into the caller."""
    if not audit_enabled():
        return
    try:
        sdk_logger = _get_logger()
        attributes = build_attributes(event_type, level, fields)
        parts = [str(fields.get("PROC") or ""), str(fields.get("MSG") or "")]
        body = " ".join(part for part in parts if part) or event_type
        sdk_logger.emit(
            body=body,
            attributes=attributes,
            severity_text=level.upper(),
            severity_number=_severity(level),
        )
    except Exception as exc:  # noqa: BLE001 — 审计不得影响业务热路径
        logger.warning("[audit] emit failed: %s: %s", type(exc).__name__, exc)


_AGENT_NAME_SPAN_ATTRS = ("jiuwenclaw.agent.name", "gen_ai.agent.name")


def current_agent_name() -> str:
    """Best-effort agent name from the active span attributes (empty when absent)."""
    try:
        span = otel_trace.get_current_span()
        attrs = getattr(span, "attributes", None) or {}
        for key in _AGENT_NAME_SPAN_ATTRS:
            value = attrs.get(key)
            if value:
                return str(value)
    except Exception as exc:
        logger.warning("[audit] current_agent_name failed: %s: %s", type(exc).__name__, exc)
    return ""


def log_event(
    *,
    submdl: str,
    proc: str,
    success: bool = True,
    uid: str | None = None,
    rspcd: str | None = None,
    desc: str | None = None,
    error: str = "",
    message: str = "",
    level: str | None = None,
    session_id: str = "",
    request_id: str = "",
    user_id: str = "",
    bot_id: str = "",
    group_id: str = "",
    extra: dict[str, Any] | None = None,
    **details: Any,
) -> None:
    """高层语义打点：按业务语义（submdl/proc/success）自动推导契约字段。

    success → UA / 失败 → EVT 自动映射；RSPCD 默认 UA=0000、EVT=placeholder；
    ``desc`` 为与关键字同名的文案；显式 id 优先于 ContextVar 快照。
    需要逐字段控制契约时用 :func:`log_audit`。
    """
    event_type = "UA" if success else "EVT"
    lvl = (level or ("INFO" if success else "WARN")).upper()
    fields: dict[str, Any] = {
        "SUBMDL": submdl,
        "PROC": proc,
        "RESULT": "success" if success else "fail",
        "MSG": error or message or "",
        event_type: desc or message or f"{submdl}.{proc} {'成功' if success else '失败'}",
        "UID": uid or "",
        "RSPCD": rspcd or ("0000" if success else ""),
        "session_id": session_id or "",
        "request_id": request_id or "",
        "user_id": user_id or "",
        "bot_id": bot_id or "",
        "group_id": group_id or "",
        "extra": {
            "agent_pod": os.getenv("HOSTNAME", ""),
            "agent_name": current_agent_name(),
            **(extra or {}),
            **details,
        },
    }
    emit(event_type, lvl, fields)


def log_audit(event_type: str, *, level: str = "INFO", **fields: Any) -> None:
    """打点入口（契约形态：字段由调用方显式给定；便捷场景用 :func:`log_event`）。

    Args:
        event_type: ``"UA"``（成功正常）| ``"EVT"``（失败/违规/异常/告警）。
        level: ``INFO`` / ``WARN`` / ``ERROR``。
        **fields: 内容要素——``SUBMDL`` / ``PROC`` 必给；可选 ``UID`` / ``SRCIP``
            / ``DSTIP`` / ``RSPCD`` / ``MSG`` / ``COST`` / ``SVRNAM``；
            与关键字同名的文案参数（``UA="..."`` 或 ``EVT="..."``）；
            ``extra`` dict 额外属性原样携带。
        缺省字段自动回退请求上下文（ContextVar）或 placeholder。
    """
    if event_type is None:
        return
    emit(event_type, level, fields)


def audit_info(event_type: str, **fields: Any) -> None:
    """INFO 级打点，语义同 :func:`log_audit`。"""
    log_audit(event_type, level="INFO", **fields)


def audit_warn(event_type: str, **fields: Any) -> None:
    """WARN 级打点（通常配 ``event_type="EVT"``）。"""
    log_audit(event_type, level="WARN", **fields)


def audit_error(event_type: str, **fields: Any) -> None:
    """ERROR 级打点（通常配 ``event_type="EVT"``）。"""
    log_audit(event_type, level="ERROR", **fields)
