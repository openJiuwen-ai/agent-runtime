# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""统一打点门面：log_audit → 组 attributes → OTEL Logs emit。"""

from __future__ import annotations

import logging
import threading
from typing import Any, Mapping

from .attributes import build_audit_attributes
from .config import AuditLogConfig, default_config
from .constants import KEYWORDS, LEVELS
from .emitter import AuditEmitter, MemoryEmitter, NoopEmitter, build_emitter
from .errors import FormatError
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
        """校验通过后替换配置，并刷新 emitter；只影响之后的新打点。"""
        if isinstance(payload, AuditLogConfig):
            cfg = payload
            cfg.validate()
        else:
            # 保留进程本地 service：payload 未带 service 时不丢
            data = dict(payload)
            with self._lock:
                if "service" not in data and self._config.service is not None:
                    data["service"] = self._config.service
            cfg = AuditLogConfig.from_dict(data, validate=True)

        with self._lock:
            old = self._emitter
            self._config = cfg
            if self._emitter_override is not None:
                self._emitter = self._emitter_override
            else:
                self._emitter = build_emitter(cfg.otel, service=cfg.service)
                if old is not self._emitter:
                    try:
                        old.shutdown()
                    except Exception:  # noqa: BLE001
                        pass

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
        body = str(
            attributes.get("UA")
            or attributes.get("EVT")
            or attributes.get("MSG")
            or keyword
        )
        try:
            emitter.emit(severity=level_norm, body=body, attributes=attributes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit emit failed: %s", exc)


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
                _default_manager._emitter.shutdown()  # noqa: SLF001
            except Exception:  # noqa: BLE001
                pass
        _default_manager = manager if manager is not None else AuditManager()
        return _default_manager


def log_audit(event_type: str, *, level: str, **fields: Any) -> None:
    get_audit_manager().log_audit(event_type, level=level, **fields)


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
    "SchemaValidationError",
    "audit_error",
    "audit_info",
    "audit_warn",
    "get_audit_manager",
    "log_audit",
    "reset_audit_manager",
)
