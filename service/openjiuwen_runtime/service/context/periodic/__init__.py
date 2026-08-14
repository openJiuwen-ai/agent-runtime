# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""进程内周期任务 SDK（Schedule + Coordinator + JobRunner）。

主备模式：``SingleLeaderCoordinator``（等待窗口抽签 + 锁续期）。
时间：Redis ``TIME`` 对表 + 本机 monotonic 推算。

对外入口：``SystemContext.create_single_leader_job``（工号取 ``ctx.instance_id``）。
"""

from .coordinator import Coordinator, SingleLeaderCoordinator
from .factory import create_single_leader_job
from .lock import TickLock
from .runner import JobRunner
from .schedule import IntervalSchedule, Schedule

__all__ = (
    "Coordinator",
    "IntervalSchedule",
    "JobRunner",
    "Schedule",
    "SingleLeaderCoordinator",
    "TickLock",
    "create_single_leader_job",
)
