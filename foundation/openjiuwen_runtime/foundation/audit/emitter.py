# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计事件写出：复用进程内 LoggerProvider（由 telemetry 安装），否则 Noop。

写出端不再由 Manager ``body.otel`` / ``AuditLogConfig.otel`` 驱动；
OTLP endpoint 与开关由部署 env + TelemetryRuntime 负责。
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .config import OtelConfig
from .constants import (
    OTEL_LOGGER_NAME,
    SERVICE_NAME_PREFIX,
)

logger = logging.getLogger(__name__)

_SEVERITY_MAP = {
    "DEBUG": 5,
    "INFO": 9,
    "WARN": 13,
    "WARNING": 13,
    "ERROR": 17,
    "FATAL": 21,
}

_missing_provider_warned = False


class AuditEmitter(Protocol):
    def emit(
        self,
        *,
        severity: str,
        body: str,
        attributes: Mapping[str, str],
    ) -> None:
        ...

    def shutdown(self) -> None:
        ...


@dataclass
class MemoryEmitter:
    """单测用：记录每次 emit。"""

    records: list[dict[str, Any]] = field(default_factory=list)

    def emit(
        self,
        *,
        severity: str,
        body: str,
        attributes: Mapping[str, str],
    ) -> None:
        self.records.append(
            {
                "severity": severity,
                "body": body,
                "attributes": dict(attributes),
            }
        )

    @staticmethod
    def shutdown() -> None:
        return None

    def clear(self) -> None:
        self.records.clear()


class NoopEmitter:
    @staticmethod
    def emit(
        *,
        severity: str,
        body: str,
        attributes: Mapping[str, str],
    ) -> None:
        return None

    @staticmethod
    def shutdown() -> None:
        return None


def resolve_service_name(service: str | None) -> str | None:
    if not service:
        return None
    name = str(service).strip()
    if not name:
        return None
    if name.startswith(SERVICE_NAME_PREFIX):
        return name
    return f"{SERVICE_NAME_PREFIX}{name}"


def _sdk_logger_provider() -> Any | None:
    """返回进程内已安装的 SDK LoggerProvider；proxy/缺省则 None。"""
    try:
        from opentelemetry import _logs as logs_api
        from opentelemetry.sdk._logs import LoggerProvider as SdkLoggerProvider
    except Exception:
        return None
    try:
        provider = logs_api.get_logger_provider()
    except Exception:
        return None
    if isinstance(provider, SdkLoggerProvider):
        return provider
    return None


class SharedProviderEmitter:
    """通过 telemetry（或其它方）已 set 的全局 LoggerProvider emit；不自建、不拥有。"""

    @staticmethod
    def emit(
        *,
        severity: str,
        body: str,
        attributes: Mapping[str, str],
    ) -> None:
        global _missing_provider_warned
        provider = _sdk_logger_provider()
        if provider is None:
            if not _missing_provider_warned:
                logger.warning(
                    "audit emit skipped: no SDK LoggerProvider "
                    "(start telemetry / set OTEL_* first)"
                )
                _missing_provider_warned = True
            return
        try:
            from opentelemetry import _logs as logs_api
            from opentelemetry._logs import SeverityNumber

            otel_logger = logs_api.get_logger(OTEL_LOGGER_NAME)
            severity_number = _SEVERITY_MAP.get(severity.upper(), 9)
            emit = getattr(otel_logger, "emit", None)
            if not callable(emit):
                logger.warning("otel logger has no emit(); drop audit record")
                return
            # 优先 kwargs emit（opentelemetry-sdk>=1.30 常见）；勿先从
            # opentelemetry.sdk._logs 导入 LogRecord（1.39+ 已移出该导出）。
            try:
                emit(
                    severity_number=SeverityNumber(severity_number),
                    severity_text=severity.upper(),
                    body=body,
                    attributes=dict(attributes),
                )
                return
            except TypeError:
                pass

            log_record_cls = _resolve_log_record_class()
            if log_record_cls is None:
                logger.warning(
                    "audit OTEL emit failed: Logger.emit kwargs unsupported "
                    "and LogRecord class not found"
                )
                return
            emit(
                log_record_cls(
                    body=body,
                    severity_number=SeverityNumber(severity_number),
                    severity_text=severity.upper(),
                    attributes=dict(attributes),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit OTEL emit failed: %s", exc)

    @staticmethod
    def shutdown() -> None:
        # Provider 由 telemetry 拥有，此处不 shutdown。
        return None


def _resolve_log_record_class() -> Any | None:
    """兼容不同 opentelemetry-sdk 版本的 LogRecord 导入路径。"""
    candidates = (
        "opentelemetry._logs.LogRecord",
        "opentelemetry.sdk._logs._internal.LogRecord",
        "opentelemetry.sdk._logs.LogRecord",
    )
    for dotted in candidates:
        module_name, _, attr = dotted.rpartition(".")
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, attr, None)
            if cls is not None:
                return cls
        except Exception:  # noqa: BLE001
            continue
    return None


def build_emitter(
    otel: OtelConfig | None = None,
    *,
    service: str | None = None,
    override: AuditEmitter | None = None,
) -> AuditEmitter:
    """装配写出端。

    ``otel`` / ``service`` 保留签名以兼容旧调用，**不再**据此自建 OTLP exporter。
    有 override 用 override；否则始终返回 SharedProviderEmitter（无 Provider 时 emit 为 no-op）。
    """
    _ = otel, service  # 兼容保留；写出由进程 Provider / 部署 env 决定
    if override is not None:
        return override
    return SharedProviderEmitter()
