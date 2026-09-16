# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""本地时钟与 timestamp 格式化（本阶段不消费 NTP）。"""

from __future__ import annotations

import time
from datetime import datetime

from .constants import TIMESTAMP_FORMAT_DEFAULT
from .errors import FormatError


class LocalClock:
    """本阶段唯一时间源：本地时钟。"""

    def now(self) -> float:
        return time.time()


def format_timestamp(ts: float, pattern: str = TIMESTAMP_FORMAT_DEFAULT) -> str:
    """将 Unix 时间戳格式化为 format.timestamp_format（默认毫秒精度）。"""
    dt = datetime.fromtimestamp(ts)
    if pattern == TIMESTAMP_FORMAT_DEFAULT:
        return dt.strftime("%Y%m%d-%H:%M:%S") + f".{dt.microsecond // 1000:03d}"
    raise FormatError(f"unsupported timestamp format: {pattern!r}")
