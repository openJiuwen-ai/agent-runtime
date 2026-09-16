# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计事件写出：OTEL Logs（optional extra）或 Noop。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .config import OtelConfig
from .constants import (
    OTEL_LOGGER_NAME,
    OTEL_PROTOCOL_GRPC,
    OTEL_PROTOCOL_HTTP,
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


class AuditEmitter(Protocol):
    def emit(
        self,
        *,
        severity: str,
        body: str,
        attributes: Mapping[str, str],
    ) -> None: ...

    def shutdown(self) -> None: ...


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

    def shutdown(self) -> None:
        return None

    def clear(self) -> None:
        self.records.clear()


class NoopEmitter:
    def emit(
        self,
        *,
        severity: str,
        body: str,
        attributes: Mapping[str, str],
    ) -> None:
        return None

    def shutdown(self) -> None:
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


def _otel_available() -> bool:
    try:
        import opentelemetry.sdk.logs  # noqa: F401
        import opentelemetry.exporter.otlp.proto.grpc._log_exporter  # noqa: F401
    except Exception:
        try:
            import opentelemetry.sdk.logs  # noqa: F401
            import opentelemetry.exporter.otlp.proto.http._log_exporter  # noqa: F401
        except Exception:
            return False
    return True


class OtelLogsEmitter:
    """通过 OTEL Logs API emit；Batch 处理器异步导出。"""

    def __init__(
        self,
        otel: OtelConfig,
        *,
        service: str | None = None,
        owns_provider: bool = False,
        logger_provider: Any = None,
        otel_logger: Any = None,
    ) -> None:
        self._otel = otel
        self._owns_provider = owns_provider
        self._provider = logger_provider
        self._logger = otel_logger

    @classmethod
    def create(
        cls,
        otel: OtelConfig,
        *,
        service: str | None = None,
    ) -> OtelLogsEmitter | NoopEmitter:
        if not otel.enabled:
            return NoopEmitter()
        if not _otel_available():
            logger.warning(
                "audit-otel extra not installed; audit emit degraded to noop"
            )
            return NoopEmitter()
        try:
            return cls._build(otel, service=service)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "failed to initialize OTEL Logs emitter: %s; degraded to noop",
                exc,
            )
            return NoopEmitter()

    @classmethod
    def _build(cls, otel: OtelConfig, *, service: str | None) -> OtelLogsEmitter:
        from opentelemetry import _logs as logs_api
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME

        existing = logs_api.get_logger_provider()
        owns_provider = False
        provider: Any = existing

        # 无可用 SDK Provider 时自建
        from opentelemetry.sdk._logs import LoggerProvider as SdkLoggerProvider

        if not isinstance(existing, SdkLoggerProvider):
            attrs: dict[str, str] = {}
            service_name = resolve_service_name(service)
            if service_name:
                attrs[SERVICE_NAME] = service_name
            resource = Resource.create(attrs) if attrs else Resource.create()
            provider = LoggerProvider(resource=resource)
            exporter = _build_otlp_log_exporter(otel)
            provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
            logs_api.set_logger_provider(provider)
            owns_provider = True

        otel_logger = logs_api.get_logger(OTEL_LOGGER_NAME)
        # LoggingHandler 仅用于确认 sdk logs 可导入；emit 走 logger.emit
        _ = LoggingHandler
        return cls(
            otel,
            service=service,
            owns_provider=owns_provider,
            logger_provider=provider,
            otel_logger=otel_logger,
        )

    def emit(
        self,
        *,
        severity: str,
        body: str,
        attributes: Mapping[str, str],
    ) -> None:
        if self._logger is None:
            return
        try:
            from opentelemetry.sdk._logs import LogRecord
            from opentelemetry._logs import SeverityNumber

            severity_number = _SEVERITY_MAP.get(severity.upper(), 9)
            # Logger.emit API（opentelemetry-sdk logs）
            emit = getattr(self._logger, "emit", None)
            if callable(emit):
                # 新 API：接受 LogRecord 或 kwargs，按版本兼容
                try:
                    emit(
                        severity_number=SeverityNumber(severity_number),
                        body=body,
                        attributes=dict(attributes),
                    )
                    return
                except TypeError:
                    record = LogRecord(
                        body=body,
                        severity_number=SeverityNumber(severity_number),
                        severity_text=severity.upper(),
                        attributes=dict(attributes),
                    )
                    emit(record)
                    return
            logger.warning("otel logger has no emit(); drop audit record")
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit OTEL emit failed: %s", exc)

    def shutdown(self) -> None:
        if self._owns_provider and self._provider is not None:
            try:
                self._provider.shutdown()
            except Exception:  # noqa: BLE001
                pass


def _build_otlp_log_exporter(otel: OtelConfig) -> Any:
    protocol = (otel.protocol or OTEL_PROTOCOL_GRPC).strip().lower()
    headers = dict(otel.headers or {})
    endpoint = otel.endpoint
    if protocol == OTEL_PROTOCOL_HTTP:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter,
        )

        return OTLPLogExporter(endpoint=endpoint, headers=headers)
    if protocol != OTEL_PROTOCOL_GRPC:
        raise ValueError(f"unsupported otel.protocol: {protocol!r}")
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

    return OTLPLogExporter(endpoint=endpoint, headers=headers)


def build_emitter(
    otel: OtelConfig,
    *,
    service: str | None = None,
    override: AuditEmitter | None = None,
) -> AuditEmitter:
    if override is not None:
        return override
    if not otel.enabled:
        return NoopEmitter()
    return OtelLogsEmitter.create(otel, service=service)
