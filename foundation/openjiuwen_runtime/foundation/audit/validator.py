# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR1.2.3 合规校验门：自定义 schema 生效前必须通过。"""

from __future__ import annotations

from typing import Any

from .clock import NtpConfig
from .constants import DEFAULT_REDLINE_FIELDS
from .errors import SchemaValidationError
from .schema import ESCAPE_MODES, AuditLogSchema, is_semver


def validate_schema(
    schema: AuditLogSchema,
    *,
    ntp: NtpConfig | None = None,
    command_force_redact: bool | None = True,
) -> None:
    """校验失败则抛 SchemaValidationError，调用方不得启用该 schema。

    红线：字段清单齐全、占位规则已声明、未弱化时间同步与脱敏。
    """
    errors: list[str] = []

    if not schema.schema_version or not is_semver(schema.schema_version):
        errors.append(
            f"schema_version must be semver like 1.0.0, got {schema.schema_version!r}"
        )

    header = schema.header
    if not header.fields:
        errors.append("header.fields must not be empty")
    if len(set(header.fields)) != len(header.fields):
        errors.append("header.fields must not contain duplicates")
    if not header.separator:
        errors.append("header.separator must be declared and non-empty")
    if header.placeholder is None or header.placeholder == "":
        errors.append("placeholder rule must be declared (non-empty placeholder)")
    if "timestamp" not in header.fields:
        errors.append("redline field timestamp must remain in header.fields")
    ts_format = (header.formats or {}).get("timestamp")
    if not ts_format:
        errors.append("header.formats.timestamp must be declared")

    content = schema.content
    if content.escape_mode not in ESCAPE_MODES:
        errors.append(
            f"content.escape_mode must be one of {ESCAPE_MODES}, got {content.escape_mode!r}"
        )
    if not content.element_separator:
        errors.append("content.element_separator must be declared")
    if not content.kv_separator:
        errors.append("content.kv_separator must be declared")
    if content.escape_mode == "replace" and not content.pipe_escape:
        errors.append("content.pipe_escape must be declared for replace mode")

    missing_redline = [
        name for name in DEFAULT_REDLINE_FIELDS if name not in schema.redline_fields
    ]
    if missing_redline:
        errors.append(
            "redline_fields must not weaken compliance; missing: "
            + ", ".join(missing_redline)
        )

    if ntp is None:
        errors.append("ntp config must be present (time sync must not be weakened)")
    else:
        try:
            interval = ntp.sync_interval_seconds
        except ValueError as exc:
            errors.append(str(exc))
            interval = 0.0
        if interval <= 0:
            errors.append("ntp.sync_interval must be positive")
        if ntp.max_offset_ms <= 0:
            errors.append("ntp.max_offset_ms must be a positive millisecond threshold")
        # servers 允许为空（部署注入）；禁止把占位符/示例地址当真源。
        for server in ntp.servers:
            if server.startswith("<") and server.endswith(">"):
                errors.append(
                    f"ntp.servers must not contain placeholder addresses: {server!r}"
                )

    if command_force_redact is False:
        errors.append("command_force_redact=False weakens FR3 redaction")

    if errors:
        raise SchemaValidationError("; ".join(errors))


def canonicalize_fields(
    incoming: dict[str, Any],
    field_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """FR1.2.4 跨 schema 接入：在边界把别名一次性映射为规范字段标识。

    内部逻辑只认 UID / RSPCD / UA 等规范名，不保留别名间接层。
    """
    aliases = field_aliases or {}
    out: dict[str, Any] = {}
    for key, value in incoming.items():
        canonical = aliases.get(key, key)
        out[canonical] = value
    return out
