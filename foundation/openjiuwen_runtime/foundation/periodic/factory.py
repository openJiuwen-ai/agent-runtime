# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""工厂：一条调用组装好主备定时任务。

对外配置只认本函数参数；内部零件（Runner / Schedule / Coordinator）不另搞 Config 袋。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from openjiuwen_runtime.foundation.periodic.coordinator.single_leader import (
    SingleLeaderCoordinator,
)
from openjiuwen_runtime.foundation.periodic.runner import JobRunner
from openjiuwen_runtime.foundation.periodic.schedule.interval import IntervalSchedule

# 内部常量（不对外暴露）
_META_TTL_SEC = 3


def create_single_leader_job(
    redis: Any,  # Redis 客户端（要能 async：set/eval/sadd…）
    *,
    name: str,  # 任务名；默认锁 key 为 lock:{name}
    on_tick: Callable[[], Awaitable[None]],  # 到点回调：async def on_tick() -> None
    instance_id: str,  # 本机实例 ID，报名/抽签用，集群内需唯一
    interval_sec: int = 1,  # 每隔多少秒响一次；锁 TTL 与此相同
    gather_window_sec: float = 0.08,  # 开火前集合窗口：提前醒来报名，到整秒抽签
    lock_key: str = "",  # 执行锁 Redis key；空则用 lock:{name}
    run_on_start: bool = False,  # True 启动后立刻跑一轮（一般仅测试）
) -> JobRunner:
    """创建主备周期任务，返回可 start/stop 的 JobRunner。

    调用方通常只需：
        job = create_single_leader_job(redis, name="x", on_tick=..., instance_id="n1")
        await job.start()
        ...
        await job.stop()
    """
    interval = max(int(interval_sec), 1)
    key = (lock_key or f"lock:{name}").rstrip(":")
    return JobRunner(
        name=name,
        schedule=IntervalSchedule(interval),
        coordinator=SingleLeaderCoordinator(
            redis,
            lock_key=key,
            lock_ttl_sec=interval,
            token_prefix=name,
            instance_id=instance_id,
            gather_window_sec=gather_window_sec,
            meta_ttl_sec=_META_TTL_SEC,
        ),
        on_tick=on_tick,
        instance_id=instance_id,
        gather_window_sec=gather_window_sec,
        run_on_start=run_on_start,
    )
