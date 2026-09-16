# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""配置预检门：失败则拒绝 apply_config / from_dict(validate=True)。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from .constants import (
    OTEL_PROTOCOLS,
    TIMESTAMP_FORMAT_DEFAULT,
)
from .errors import SchemaValidationError
from .schema import FormatSpec, is_semver

if TYPE_CHECKING:
    from .config import AuditLogConfig, OtelConfig


def validate_format(fmt: FormatSpec) -> None:
    errors: list[str] = []

    if not fmt.schema_version or not is_semver(fmt.schema_version):
        errors.append(
            f"schema_version must be semver like 1.0.0, got {fmt.schema_version!r}"
        )

    if not fmt.header_fields:
        errors.append("format.header_fields must not be empty")
    if len(set(fmt.header_fields)) != len(fmt.header_fields):
        errors.append("format.header_fields must not contain duplicates")

    if not fmt.content_fields:
        errors.append("format.content_fields must not be empty")
    if len(set(fmt.content_fields)) != len(fmt.content_fields):
        errors.append("format.content_fields must not contain duplicates")

    if not fmt.required_fields:
        errors.append("format.required_fields must not be empty")
    if len(set(fmt.required_fields)) != len(fmt.required_fields):
        errors.append("format.required_fields must not contain duplicates")

    if fmt.placeholder is None or fmt.placeholder == "":
        errors.append("format.placeholder must be declared (non-empty)")

    if fmt.timestamp_format != TIMESTAMP_FORMAT_DEFAULT:
        errors.append(
            f"format.timestamp_format must be {TIMESTAMP_FORMAT_DEFAULT!r} "
            f"in this phase, got {fmt.timestamp_format!r}"
        )

    enabled = set(fmt.header_fields) | set(fmt.content_fields)
    missing = [name for name in fmt.required_fields if name not in enabled]
    if missing:
        errors.append(
            "format.required_fields must be subset of header_fields ∪ content_fields; "
            "missing from enabled: " + ", ".join(missing)
        )

    if errors:
        raise SchemaValidationError("; ".join(errors))


def validate_otel(otel: OtelConfig) -> None:
    errors: list[str] = []
    if not isinstance(otel.enabled, bool):
        errors.append("otel.enabled must be a boolean")
    protocol = str(otel.protocol or "").strip().lower()
    if protocol not in OTEL_PROTOCOLS:
        errors.append(
            f"otel.protocol must be one of {OTEL_PROTOCOLS}, got {otel.protocol!r}"
        )
    if otel.enabled and not str(otel.endpoint or "").strip():
        errors.append("otel.endpoint must be non-empty when otel.enabled=true")
    if otel.headers is not None and not isinstance(otel.headers, Mapping):
        errors.append("otel.headers must be a mapping")
    if errors:
        raise SchemaValidationError("; ".join(errors))


def validate_ntp_shape(ntp_data: Any) -> None:
    """宽松形状校验：有 ntp 则须为 mapping；本阶段不做语义门禁。"""
    if ntp_data is None:
        return
    if not isinstance(ntp_data, Mapping):
        raise SchemaValidationError("ntp must be an object when present")


def validate_config(config: AuditLogConfig) -> None:
    validate_format(config.format)
    validate_otel(config.otel)


def canonicalize_fields(
    incoming: dict[str, Any],
    field_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """边界把别名一次性映射为规范字段标识。"""
    aliases = field_aliases or {}
    out: dict[str, Any] = {}
    for key, value in incoming.items():
        out[aliases.get(key, key)] = value
    return out
