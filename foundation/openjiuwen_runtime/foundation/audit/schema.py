# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志 format：字段清单（本阶段不再使用管道 schema）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .constants import (
    DEFAULT_CONTENT_FIELDS,
    DEFAULT_HEADER_FIELDS,
    DEFAULT_REQUIRED_FIELDS,
    PLACEHOLDER_DEFAULT,
    SCHEMA_VERSION_DEFAULT,
    TIMESTAMP_FORMAT_DEFAULT,
)

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def is_semver(value: str) -> bool:
    return bool(_SEMVER_RE.match(value))


@dataclass(frozen=True)
class FormatSpec:
    """决定每条审计 LogRecord 携带哪些属性键。"""

    schema_version: str = SCHEMA_VERSION_DEFAULT
    header_fields: tuple[str, ...] = DEFAULT_HEADER_FIELDS
    content_fields: tuple[str, ...] = DEFAULT_CONTENT_FIELDS
    required_fields: tuple[str, ...] = DEFAULT_REQUIRED_FIELDS
    placeholder: str = PLACEHOLDER_DEFAULT
    timestamp_format: str = TIMESTAMP_FORMAT_DEFAULT

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> FormatSpec:
        if not data:
            return cls()
        return cls(
            schema_version=str(data.get("schema_version", SCHEMA_VERSION_DEFAULT)),
            header_fields=tuple(data.get("header_fields", DEFAULT_HEADER_FIELDS)),
            content_fields=tuple(data.get("content_fields", DEFAULT_CONTENT_FIELDS)),
            required_fields=tuple(data.get("required_fields", DEFAULT_REQUIRED_FIELDS)),
            placeholder=str(data.get("placeholder", PLACEHOLDER_DEFAULT)),
            timestamp_format=str(
                data.get("timestamp_format", TIMESTAMP_FORMAT_DEFAULT)
            ),
        )

    @property
    def enabled_fields(self) -> tuple[str, ...]:
        """header ∪ content，保持声明顺序且去重。"""
        seen: set[str] = set()
        out: list[str] = []
        for name in (*self.header_fields, *self.content_fields):
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
        return tuple(out)


def default_format() -> FormatSpec:
    return FormatSpec()
