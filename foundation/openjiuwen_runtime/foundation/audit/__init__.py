# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""安全审计日志（foundation.audit）。

本阶段：字段清单 format + ContextVar 合并 + OTEL Logs emit（复用 telemetry LoggerProvider）。
otel endpoint/开关由部署 env 配置，不由 Manager 下发驱动。
不做：本地审计文件、管道行、NTP 同步、脱敏、SIEM。
"""

from __future__ import annotations

from .attributes import build_audit_attributes, capture_caller
from .clock import LocalClock, format_timestamp
from .config import (
    AuditLogConfig,
    NtpConfig,
    OtelConfig,
    RuntimeIdentityConfig,
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
    SUBMDL_AGENT,
    SUBMDL_ALERT,
    SUBMDL_API_CLIENT,
    SUBMDL_FILE,
    SUBMDL_GATEWAY,
    SUBMDL_SANDBOX,
    TIMESTAMP_FORMAT_DEFAULT,
)
from .context import (
    AuditContextTokens,
    audit_context_snapshot,
    bind_audit_context,
    bind_request_context,
    bind_routing,
    clear_audit_context,
    get_audit_context,
    lookup_routing,
    reset_audit_context,
    reset_request_context,
)
from .emitter import (
    MemoryEmitter,
    NoopEmitter,
    SharedProviderEmitter,
    build_emitter,
    resolve_service_name,
)
from .errors import AuditError, FormatError, SchemaValidationError
from .facade import (
    AuditManager,
    audit_error,
    audit_info,
    audit_warn,
    get_audit_manager,
    log_audit,
    log_event,
    reset_audit_manager,
)
from .models import AuditSnapshot, RuntimeIdentity
from .schema import FormatSpec, default_format, is_semver
from .validator import canonicalize_fields, validate_config, validate_format, validate_otel

__all__ = (
    "ATTRIBUTE_VALUE_MAX_LENGTH_DEFAULT",
    "AuditContextTokens",
    "AuditError",
    "AuditLogConfig",
    "AuditManager",
    "AuditSnapshot",
    "DEFAULT_CONTENT_FIELDS",
    "DEFAULT_HEADER_FIELDS",
    "DEFAULT_REQUIRED_FIELDS",
    "EVENT_TYPE_ATTR",
    "FormatError",
    "FormatSpec",
    "KEYWORD_EVT",
    "KEYWORD_UA",
    "LEVELS",
    "LocalClock",
    "MemoryEmitter",
    "NoopEmitter",
    "NtpConfig",
    "OTEL_LOGGER_NAME",
    "OtelConfig",
    "PLACEHOLDER_DEFAULT",
    "RSPCD_KNOWN",
    "RuntimeIdentity",
    "RuntimeIdentityConfig",
    "SCHEMA_VERSION_DEFAULT",
    "SUBMDL_AGENT",
    "SUBMDL_ALERT",
    "SUBMDL_API_CLIENT",
    "SUBMDL_FILE",
    "SUBMDL_GATEWAY",
    "SUBMDL_SANDBOX",
    "SchemaValidationError",
    "SharedProviderEmitter",
    "TIMESTAMP_FORMAT_DEFAULT",
    "audit_context_snapshot",
    "audit_error",
    "audit_info",
    "audit_warn",
    "bind_audit_context",
    "bind_request_context",
    "bind_routing",
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
    "log_event",
    "lookup_routing",
    "reset_audit_context",
    "reset_audit_manager",
    "reset_request_context",
    "resolve_service_name",
    "validate_config",
    "validate_format",
    "validate_otel",
)
