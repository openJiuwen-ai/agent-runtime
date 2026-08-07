# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Sweeper 配置（P0 + P1）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SweeperConfig:
    """老化与回收定时任务配置（对齐 SWEEPER_DESIGN）。"""

    enabled: bool = True
    interval_sec: int = 1
    lock_key: str = "lock:sweep"
    lock_ttl_sec: int = 1
    lock_token_prefix: str = "sweeper"
    idle_notify_ttl_sec: int = 60
    resource_idle_consider_path: str = "/resource/idle_consider"
    resource_base_url: str = ""
    stop_timeout_sec: float = 5.0
