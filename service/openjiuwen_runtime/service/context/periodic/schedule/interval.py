# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""固定间隔、挂钟边界对齐的调度。"""

from __future__ import annotations

import math


class IntervalSchedule:
    """下一个 interval 边界触发（整秒/整 N 秒对齐）。"""

    def __init__(self, interval_sec: int = 1) -> None:
        self._interval_sec = max(int(interval_sec), 1)

    @property
    def interval_sec(self) -> int:
        return self._interval_sec

    def next_fire_time(self, now: float) -> float:
        interval = self._interval_sec
        return (math.floor(now / interval) + 1) * interval
