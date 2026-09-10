# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR3 脱敏（尚未实现）。"""

from __future__ import annotations

from typing import Any

from .config import RedactionConfig
from .errors import AuditNotImplementedError


def redact(value: Any, config: RedactionConfig | None = None) -> Any:
    """对敏感参数按键名 / 形态脱敏。见 SRS FR3。"""
    raise AuditNotImplementedError("FR3 redaction is not implemented")
