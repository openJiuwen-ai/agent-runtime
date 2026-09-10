# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""审计日志运行时模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class RuntimeIdentity:
    """部署身份，写入头部 data_center / system_code / node。"""

    data_center: str = "-"
    system_code: str = "-"
    node: str = "-"


@dataclass
class ParsedAuditLine:
    """parse_audit_line 的结果，便于单测断言。"""

    header: dict[str, str]
    keyword: str | None
    elements: dict[str, str]
    ext: dict[str, str] = field(default_factory=dict)
    raw: str = ""


@dataclass(frozen=True)
class ClockEvent:
    """时钟同步产生的 #EVT 语义（由 formatter 落成管道行）。"""

    event: str
    level: str
    message: str
    rspcd: str
    details: Mapping[str, Any] = field(default_factory=dict)
