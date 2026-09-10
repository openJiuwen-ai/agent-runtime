# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志 schema（FR1）：头部 schema + 内容 schema。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .constants import (
    CONTENT_ELEMENT_SEPARATOR_DEFAULT,
    CONTENT_KEYWORD_PREFIX_DEFAULT,
    CONTENT_KV_SEPARATOR_DEFAULT,
    DEFAULT_HEADER_FIELDS,
    DEFAULT_REDLINE_FIELDS,
    ESCAPE_MODE_ESCAPE,
    ESCAPE_MODE_REPLACE,
    HEADER_SEPARATOR_DEFAULT,
    PIPE_ESCAPE_DEFAULT,
    PLACEHOLDER_DEFAULT,
    SCHEMA_VERSION_DEFAULT,
    TIMESTAMP_FORMAT_DEFAULT,
)

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def is_semver(value: str) -> bool:
    return bool(_SEMVER_RE.match(value))


@dataclass(frozen=True)
class HeaderSchema:
    """头部 schema：字段集、顺序、分隔符、占位符、字段格式化规则。"""

    fields: tuple[str, ...] = DEFAULT_HEADER_FIELDS
    separator: str = HEADER_SEPARATOR_DEFAULT
    placeholder: str = PLACEHOLDER_DEFAULT
    formats: Mapping[str, str] = field(
        default_factory=lambda: {"timestamp": TIMESTAMP_FORMAT_DEFAULT}
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> HeaderSchema:
        if not data:
            return cls()
        fields = data.get("fields", DEFAULT_HEADER_FIELDS)
        formats = data.get("formats") or {"timestamp": TIMESTAMP_FORMAT_DEFAULT}
        return cls(
            fields=tuple(fields),
            separator=str(data.get("separator", HEADER_SEPARATOR_DEFAULT)),
            placeholder=str(data.get("placeholder", PLACEHOLDER_DEFAULT)),
            formats=dict(formats),
        )


@dataclass(frozen=True)
class ContentSchema:
    """内容 schema：定义「内容怎么拼」，不枚举业务要素。"""

    keyword_prefix: str = CONTENT_KEYWORD_PREFIX_DEFAULT
    element_separator: str = CONTENT_ELEMENT_SEPARATOR_DEFAULT
    kv_separator: str = CONTENT_KV_SEPARATOR_DEFAULT
    escape_mode: str = ESCAPE_MODE_REPLACE
    pipe_escape: str = PIPE_ESCAPE_DEFAULT
    keyword_required: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ContentSchema:
        if not data:
            return cls()
        return cls(
            keyword_prefix=str(data.get("keyword_prefix", CONTENT_KEYWORD_PREFIX_DEFAULT)),
            element_separator=str(
                data.get("element_separator", CONTENT_ELEMENT_SEPARATOR_DEFAULT)
            ),
            kv_separator=str(data.get("kv_separator", CONTENT_KV_SEPARATOR_DEFAULT)),
            escape_mode=str(data.get("escape_mode", ESCAPE_MODE_REPLACE)),
            pipe_escape=str(data.get("pipe_escape", PIPE_ESCAPE_DEFAULT)),
            keyword_required=bool(data.get("keyword_required", False)),
        )


@dataclass(frozen=True)
class AuditLogSchema:
    """一条审计日志的完整格式定义（头部 + 内容 + 红线清单）。"""

    schema_version: str = SCHEMA_VERSION_DEFAULT
    header: HeaderSchema = field(default_factory=HeaderSchema)
    content: ContentSchema = field(default_factory=ContentSchema)
    redline_fields: tuple[str, ...] = DEFAULT_REDLINE_FIELDS

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> AuditLogSchema:
        if not data:
            return cls()
        return cls(
            schema_version=str(data.get("schema_version", SCHEMA_VERSION_DEFAULT)),
            header=HeaderSchema.from_dict(data.get("header")),
            content=ContentSchema.from_dict(data.get("content")),
            redline_fields=tuple(data.get("redline_fields", DEFAULT_REDLINE_FIELDS)),
        )


def default_schema() -> AuditLogSchema:
    return AuditLogSchema()


ESCAPE_MODES: tuple[str, ...] = (ESCAPE_MODE_REPLACE, ESCAPE_MODE_ESCAPE)
