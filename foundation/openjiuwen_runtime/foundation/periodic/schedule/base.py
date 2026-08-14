# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""调度器协议。"""

from __future__ import annotations

from typing import Protocol


class Schedule(Protocol):
    def next_fire_time(self, now: float) -> float:
        """返回严格大于 now 的下次触发 unix 秒。"""
        ...
