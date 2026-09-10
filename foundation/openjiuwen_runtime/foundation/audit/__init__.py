# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""安全审计日志（foundation.audit）。

当前实现 SRS FR1（schema 驱动格式 + 合规校验门 + NTP 时钟）。
FR2–FR8 仅提供配置骨架与接口占位，见 README.md / DESIGN.md。
"""

from __future__ import annotations

from .clock import (
    LocalClock,
    NtpClient,
    NtpConfig,
    SequenceNtpClient,
    SyncResult,
    SyncedClock,
    UdpNtpClient,
    parse_sync_interval,
)
from .config import (
    AccessConfig,
    AuditLogConfig,
    DeployConfig,
    LinkpointConfig,
    RedactionConfig,
    RuntimeIdentityConfig,
    ShipperConfig,
    StorageConfig,
    WriterConfig,
    default_config,
)
from .constants import (
    CONTENT_REQUIRED_FIELDS,
    DEFAULT_HEADER_FIELDS,
    DEFAULT_REDLINE_FIELDS,
    KEYWORD_EVT,
    KEYWORD_UA,
    LEVELS,
    RSPCD_KNOWN,
    SCHEMA_VERSION_DEFAULT,
)
from .errors import (
    AuditError,
    AuditNotImplementedError,
    ClockSyncError,
    FormatError,
    SchemaValidationError,
)
from .facade import AuditFacade, log_audit
from .formatter import (
    capture_caller,
    escape_value,
    format_audit_line,
    format_clock_event,
    format_timestamp,
    parse_audit_line,
)
from .models import ClockEvent, ParsedAuditLine, RuntimeIdentity
from .schema import AuditLogSchema, ContentSchema, HeaderSchema, default_schema
from .validator import canonicalize_fields, validate_schema

__all__ = (
    "AccessConfig",
    "AuditError",
    "AuditFacade",
    "AuditLogConfig",
    "AuditLogSchema",
    "AuditNotImplementedError",
    "CONTENT_REQUIRED_FIELDS",
    "ClockEvent",
    "ClockSyncError",
    "ContentSchema",
    "DEFAULT_HEADER_FIELDS",
    "DEFAULT_REDLINE_FIELDS",
    "DeployConfig",
    "FormatError",
    "HeaderSchema",
    "KEYWORD_EVT",
    "KEYWORD_UA",
    "LEVELS",
    "LinkpointConfig",
    "LocalClock",
    "NtpClient",
    "NtpConfig",
    "ParsedAuditLine",
    "RSPCD_KNOWN",
    "RedactionConfig",
    "RuntimeIdentity",
    "RuntimeIdentityConfig",
    "SCHEMA_VERSION_DEFAULT",
    "SchemaValidationError",
    "SequenceNtpClient",
    "ShipperConfig",
    "StorageConfig",
    "SyncResult",
    "SyncedClock",
    "UdpNtpClient",
    "WriterConfig",
    "canonicalize_fields",
    "capture_caller",
    "default_config",
    "default_schema",
    "escape_value",
    "format_audit_line",
    "format_clock_event",
    "format_timestamp",
    "log_audit",
    "parse_audit_line",
    "parse_sync_interval",
    "validate_schema",
)
