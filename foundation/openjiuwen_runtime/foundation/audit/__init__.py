# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""安全审计日志（foundation.audit）。

本阶段：字段清单 format + ContextVar 合并 + OTEL Logs emit（optional extra ``audit-otel``）。
不做：本地审计文件、管道行、NTP 同步、脱敏、SIEM。
"""

from __future__ import annotations

from .attributes import build_audit_attributes, capture_caller
from .clock import LocalClock, format_timestamp
from .config import (
    AccessConfig,
    AuditLogConfig,
    DeployConfig,
    LinkpointConfig,
    NtpConfig,
    OtelConfig,
    RedactionConfig,
    RuntimeIdentityConfig,
    ShipperConfig,
    StorageConfig,
    WriterConfig,
    default_config,
)
from .constants import (
    ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT,
    DEFAULT_CONTENT_FIELDS,
    DEFAULT_HEADER_FIELDS,
    DEFAULT_REQUIRED_FIELDS,
    EVENT_TYPE_ATTR,
    KEYWORD_EVT,
    KEYWORD_UA,
    LEVELS,
    OTEL_LOGGER_NAME,
    PLACEHOLDER_DEFAULT,
    RSPCD_KNOWN,
    SCHEMA_VERSION_DEFAULT,
    TIMESTAMP_FORMAT_DEFAULT,
)
from .context import (
    AuditContextTokens,
    bind_audit_context,
    clear_audit_context,
    get_audit_context,
    reset_audit_context,
)
from .emitter import MemoryEmitter, NoopEmitter, build_emitter, resolve_service_name
from .errors import (
    AuditError,
    AuditNotImplementedError,
    FormatError,
    SchemaValidationError,
)
from .facade import (
    AuditManager,
    audit_error,
    audit_info,
    audit_warn,
    get_audit_manager,
    log_audit,
    reset_audit_manager,
)
from .models import AuditSnapshot, RuntimeIdentity
from .schema import FormatSpec, default_format, is_semver
from .validator import canonicalize_fields, validate_config, validate_format, validate_otel

__all__ = (
    "ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT",
    "AccessConfig",
    "AuditContextTokens",
    "AuditError",
    "AuditLogConfig",
    "AuditManager",
    "AuditNotImplementedError",
    "AuditSnapshot",
    "DEFAULT_CONTENT_FIELDS",
    "DEFAULT_HEADER_FIELDS",
    "DEFAULT_REQUIRED_FIELDS",
    "DeployConfig",
    "EVENT_TYPE_ATTR",
    "FormatError",
    "FormatSpec",
    "KEYWORD_EVT",
    "KEYWORD_UA",
    "LEVELS",
    "LinkpointConfig",
    "LocalClock",
    "MemoryEmitter",
    "NoopEmitter",
    "NtpConfig",
    "OTEL_LOGGER_NAME",
    "OtelConfig",
    "PLACEHOLDER_DEFAULT",
    "RSPCD_KNOWN",
    "RedactionConfig",
    "RuntimeIdentity",
    "RuntimeIdentityConfig",
    "SCHEMA_VERSION_DEFAULT",
    "SchemaValidationError",
    "ShipperConfig",
    "StorageConfig",
    "TIMESTAMP_FORMAT_DEFAULT",
    "WriterConfig",
    "audit_error",
    "audit_info",
    "audit_warn",
    "bind_audit_context",
    "build_audit_attributes",
    "build_emitter",
    "canonicalize_fields",
    "capture_caller",
    "clear_audit_context",
    "default_config",
    "default_format",
    "format_timestamp",
    "get_audit_context",
    "get_audit_manager",
    "is_semver",
    "log_audit",
    "reset_audit_context",
    "reset_audit_manager",
    "resolve_service_name",
    "validate_config",
    "validate_format",
    "validate_otel",
)
