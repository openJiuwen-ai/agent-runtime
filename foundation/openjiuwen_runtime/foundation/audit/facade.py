# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""统一打点门面：log_audit / log_event → 组 attributes → OTEL Logs emit。

写出端由进程内 LoggerProvider（telemetry）提供；apply_config 只更新
format / identity，不再按 body.otel 重建 exporter。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Mapping

from .attributes import build_audit_attributes
from .config import AuditLogConfig, default_config
from .constants import KEYWORDS, KEYWORD_EVT, KEYWORD_UA, LEVELS, RSPCD_SUCCESS
from .emitter import AuditEmitter, MemoryEmitter, NoopEmitter, SharedProviderEmitter, build_emitter
from .errors import FormatError, SchemaValidationError
from .models import AuditSnapshot

logger = logging.getLogger(__name__)

_manager_lock = threading.RLock()
_default_manager: "AuditManager | None" = None


class AuditManager:
    """进程内审计门面（单例由 get_audit_manager 管理）。"""

    def __init__(
        self,
        config: AuditLogConfig | None = None,
        *,
        emitter: AuditEmitter | None = None,
    ) -> None:
        self._lock = threading.RLock()
        cfg = config or default_config()
        cfg.validate()
        self._config = cfg
        self._emitter_override = emitter
        self._emitter: AuditEmitter = build_emitter(
            cfg.otel,
            service=cfg.service,
            override=emitter,
        )

    @property
    def config(self) -> AuditLogConfig:
        with self._lock:
            return self._config

    def snapshot(self) -> AuditSnapshot:
        with self._lock:
            return AuditSnapshot.from_config(self._config)

    def apply_config(self, payload: Mapping[str, Any] | AuditLogConfig) -> None:
        """校验通过后替换 format/identity 等配置；不重建写出端（otel 由部署/telemetry 管）。"""
        if isinstance(payload, AuditLogConfig):
            cfg = payload
            cfg.validate()
        else:
            data = dict(payload)
            with self._lock:
                if "service" not in data and self._config.service is not None:
                    data["service"] = self._config.service
                # 保留库内/旧客户端可能带来的 otel 块以便 from_dict 不炸；不驱动 emitter
                if "otel" not in data:
                    data["otel"] = {
                        "enabled": self._config.otel.enabled,
                        "endpoint": self._config.otel.endpoint,
                        "protocol": self._config.otel.protocol,
                        "headers": dict(self._config.otel.headers),
                    }
            cfg = AuditLogConfig.from_dict(data, validate=True)

        with self._lock:
            self._config = cfg
            # 写出端：有测试 override 则保持；否则保持 SharedProviderEmitter，不因 otel 重建

    def set_emitter(self, emitter: AuditEmitter) -> None:
        """测试或进程装配注入写出端。"""
        with self._lock:
            old = self._emitter
            self._emitter_override = emitter
            self._emitter = emitter
            if old is not emitter:
                try:
                    old.shutdown()
                except Exception:  # noqa: BLE001
                    pass

    def shutdown(self) -> None:
        """关闭当前写出端（测试重置单例时调用）。"""
        with self._lock:
            emitter = self._emitter
        try:
            emitter.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def log_audit(self, event_type: str, *, level: str, **fields: Any) -> None:
        """组装 attributes 并 emit。调用方错误（缺 level / 非法 event_type）抛 FormatError。"""
        keyword = str(event_type or "").strip().upper()
        if keyword not in KEYWORDS:
            raise FormatError(
                f"event_type must be one of {KEYWORDS}, got {event_type!r}"
            )
        if level is None or str(level).strip() == "":
            raise FormatError("level is required")
        level_norm = str(level).strip().upper()
        if level_norm == "WARNING":
            level_norm = "WARN"
        if level_norm not in LEVELS:
            raise FormatError(f"level must be one of {LEVELS}, got {level!r}")

        snap = self.snapshot()
        with self._lock:
            emitter = self._emitter

        attributes = build_audit_attributes(
            snap,
            event_type=keyword,
            level=level_norm,
            fields=fields,
        )
        body = _select_body(attributes, event_type=keyword, placeholder=snap.format.placeholder)
        try:
            emitter.emit(severity=level_norm, body=body, attributes=attributes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit emit failed: %s", exc)

    def log_event(
        self,
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
        """高层语义打点：success→UA / 失败→EVT，再交给 log_audit。"""
        event_type = KEYWORD_UA if success else KEYWORD_EVT
        lvl = (level or ("INFO" if success else "WARN")).upper()
        text = desc or message or (
            f"{submdl}.{proc} {'成功' if success else '失败'}"
        )
        fields: dict[str, Any] = {
            "SUBMDL": submdl,
            "PROC": proc,
            "RESULT": "success" if success else "fail",
            "MSG": error or message or "",
            event_type: text,
            "UID": uid or user_id or "",
            "RSPCD": rspcd if rspcd is not None else (RSPCD_SUCCESS if success else ""),
            "session_id": session_id or "",
            "request_id": request_id or "",
            "user_id": user_id or uid or "",
            "bot_id": bot_id or "",
            "group_id": group_id or "",
            "extra": {
                "agent_pod": os.getenv("HOSTNAME", ""),
                **(extra or {}),
                **details,
            },
        }
        self.log_audit(event_type, level=lvl, **fields)


def _select_body(
    attributes: Mapping[str, str],
    *,
    event_type: str,
    placeholder: str,
) -> str:
    """按 event_type 优先取 UA/EVT，再 MSG；占位符视为无正文。"""
    preferred = ("UA", "MSG") if event_type == "UA" else ("EVT", "MSG")
    ordered = preferred + (("EVT",) if event_type == "UA" else ("UA",))
    for key in ordered:
        value = attributes.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != placeholder:
            return text
    return event_type


def get_audit_manager() -> AuditManager:
    global _default_manager
    with _manager_lock:
        if _default_manager is None:
            _default_manager = AuditManager()
        return _default_manager


def reset_audit_manager(manager: AuditManager | None = None) -> AuditManager:
    """测试辅助：替换或重建进程单例。"""
    global _default_manager
    with _manager_lock:
        if _default_manager is not None and manager is None:
            try:
                _default_manager.shutdown()
            except Exception:  # noqa: BLE001
                pass
        _default_manager = manager if manager is not None else AuditManager()
        return _default_manager


def log_audit(event_type: str, *, level: str, **fields: Any) -> None:
    get_audit_manager().log_audit(event_type, level=level, **fields)


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
    get_audit_manager().log_event(
        submdl=submdl,
        proc=proc,
        success=success,
        uid=uid,
        rspcd=rspcd,
        desc=desc,
        error=error,
        message=message,
        level=level,
        session_id=session_id,
        request_id=request_id,
        user_id=user_id,
        bot_id=bot_id,
        group_id=group_id,
        extra=extra,
        **details,
    )


def audit_info(event_type: str, **fields: Any) -> None:
    log_audit(event_type, level="INFO", **fields)


def audit_warn(event_type: str, **fields: Any) -> None:
    log_audit(event_type, level="WARN", **fields)


def audit_error(event_type: str, **fields: Any) -> None:
    log_audit(event_type, level="ERROR", **fields)


__all__ = (
    "AuditManager",
    "MemoryEmitter",
    "NoopEmitter",
    "SharedProviderEmitter",
    "SchemaValidationError",
    "audit_error",
    "audit_info",
    "audit_warn",
    "get_audit_manager",
    "log_audit",
    "log_event",
    "reset_audit_manager",
)
