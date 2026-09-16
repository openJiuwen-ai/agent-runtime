# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志配置（format / otel / identity；ntp 仅预留解析）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .constants import (
    ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT,
    NTP_FAILOVER_DEFAULT,
    NTP_MAX_OFFSET_MS_DEFAULT,
    NTP_SYNC_INTERVAL_DEFAULT,
    OTEL_PROTOCOL_GRPC,
)
from .schema import FormatSpec
from .validator import validate_config


@dataclass(frozen=True)
class RuntimeIdentityConfig:
    data_center: str = "-"
    system_code: str = "-"
    node: str = "-"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> RuntimeIdentityConfig:
        if not data:
            return cls()
        return cls(
            data_center=str(data.get("data_center", "-")),
            system_code=str(data.get("system_code", "-")),
            node=str(data.get("node", "-")),
        )


@dataclass(frozen=True)
class NtpConfig:
    """NTP 配置预留：本阶段可随 body 落库，SDK 不消费。"""

    servers: tuple[str, ...] = ()
    sync_interval: str = NTP_SYNC_INTERVAL_DEFAULT
    max_offset_ms: int = NTP_MAX_OFFSET_MS_DEFAULT
    failover: bool = NTP_FAILOVER_DEFAULT

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> NtpConfig:
        if not data:
            return cls()
        if not isinstance(data, Mapping):
            return cls()
        servers = data.get("servers") or ()
        return cls(
            servers=tuple(str(s) for s in servers if str(s).strip()),
            sync_interval=str(data.get("sync_interval", NTP_SYNC_INTERVAL_DEFAULT)),
            max_offset_ms=int(data.get("max_offset_ms", NTP_MAX_OFFSET_MS_DEFAULT)),
            failover=bool(data.get("failover", NTP_FAILOVER_DEFAULT)),
        )


@dataclass(frozen=True)
class OtelConfig:
    """OTEL Logs 连接；service.name 由进程本地 service 推导，不在此下发。"""

    enabled: bool = True
    endpoint: str = "http://localhost:4317"
    protocol: str = OTEL_PROTOCOL_GRPC
    headers: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> OtelConfig:
        if not data:
            return cls()
        headers = data.get("headers") or {}
        if not isinstance(headers, Mapping):
            headers = {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            endpoint=str(data.get("endpoint", "http://localhost:4317")),
            protocol=str(data.get("protocol", OTEL_PROTOCOL_GRPC)).strip().lower(),
            headers={str(k): str(v) for k, v in headers.items()},
        )


# --- FR3–FR7 骨架配置（本阶段主路径不消费；供骨架模块 import）---


@dataclass(frozen=True)
class RedactionConfig:
    redaction_token: str = "***REDACTED***"
    sensitive_keys: tuple[str, ...] = (
        "password",
        "token",
        "secret",
        "key",
        "cvn",
        "pin",
        "cvn2",
        "otp",
        "captcha",
        "smscode",
        "dynamic_code",
        "verify_code",
    )
    command_force_redact: bool = True
    sandbox_path_redact: bool = True


@dataclass(frozen=True)
class WriterConfig:
    async_write: bool = True
    queue_size: int = 10000


@dataclass(frozen=True)
class StorageConfig:
    filename_pattern: str = "SEC-{dc}_{sys}_{node}.log"
    storage_dir: str = "/var/log/audit/"


@dataclass(frozen=True)
class ShipperConfig:
    protocol: str = ""
    endpoint: str = ""


@dataclass(frozen=True)
class AccessConfig:
    require_auth: bool = True


@dataclass(frozen=True)
class DeployConfig:
    precheck_gate: bool = True


@dataclass(frozen=True)
class LinkpointConfig:
    sandbox_submdl: str = "sandbox"


@dataclass(frozen=True)
class AuditLogConfig:
    """完整审计配置。本阶段生效：format / otel / identity / service。"""

    format: FormatSpec = field(default_factory=FormatSpec)
    otel: OtelConfig = field(default_factory=OtelConfig)
    identity: RuntimeIdentityConfig = field(default_factory=RuntimeIdentityConfig)
    ntp: NtpConfig = field(default_factory=NtpConfig)
    service: str | None = None
    attribute_value_max_length: int = ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any] | None,
        *,
        validate: bool = True,
    ) -> AuditLogConfig:
        data = dict(data or {})
        fmt_data = data.get("format")
        if not isinstance(fmt_data, Mapping):
            fmt_data = None

        identity_data = {
            "data_center": data.get("data_center", "-"),
            "system_code": data.get("system_code", "-"),
            "node": data.get("node", "-"),
        }

        service = data.get("service")
        service_str = str(service).strip() if service is not None else None
        if service_str == "":
            service_str = None

        max_len = data.get(
            "attribute_value_max_length", ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT
        )
        try:
            max_len_int = max(1, int(max_len))
        except (TypeError, ValueError):
            max_len_int = ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT

        cfg = cls(
            format=FormatSpec.from_dict(fmt_data),
            otel=OtelConfig.from_dict(
                data.get("otel") if isinstance(data.get("otel"), Mapping) else None
            ),
            identity=RuntimeIdentityConfig.from_dict(identity_data),
            ntp=NtpConfig.from_dict(
                data.get("ntp") if isinstance(data.get("ntp"), Mapping) else None
            ),
            service=service_str,
            attribute_value_max_length=max_len_int,
        )
        if validate:
            cfg.validate()
        return cfg

    def validate(self) -> None:
        validate_config(self)


def default_config(*, validate: bool = True) -> AuditLogConfig:
    cfg = AuditLogConfig()
    if validate:
        cfg.validate()
    return cfg
