# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""工厂：一条调用组装好主备定时任务。

对外配置只认本函数参数；内部零件（Runner / Schedule / Coordinator）不另搞 Config 袋。
redis / instance_id 一律从 ``SystemContext`` 取，不单独传入。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .clock import RedisAlignedClock
from .coordinator.single_leader import SingleLeaderCoordinator
from .runner import JobRunner
from .schedule.interval import IntervalSchedule

if TYPE_CHECKING:
    from ..system_context import SystemContext

# 内部常量（不对外暴露）
_META_TTL_FLOOR_SEC = 3

# 执行锁 TTL 下限（2026-11）：锁 TTL 原本恒等于调度间隔，1s 间隔的任务于是拿到
# 1s 租约、每 0.33s 续一次——高延迟/公用 Redis 上任何一次续期往返超过 1s（无 Lua
# 模式的 CAS 续期要 3~4 个往返）就失锁，正在跑的那一拍被 abort。实测现网
# 1Hz 任务因此反复 renew_not_owner + on_tick aborted。租约在每拍结束必 release，
# 故抬高下限只放大「进程被 SIGKILL 后该任务最多停多久」这一个窗口。
_LOCK_TTL_FLOOR_SEC = 10


def create_single_leader_job(
    ctx: "SystemContext",
    *,
    name: str,  # 任务名；默认锁 key 为 lock:{name}
    on_tick: Callable[[], Awaitable[None]],  # 到点回调：async def on_tick() -> None
    interval_sec: int = 1,  # 每隔多少秒响一次
    gather_window_sec: float = 0.08,  # 开火前集合窗口：提前醒来报名，到整秒抽签
    lock_key: str = "",  # 执行锁 Redis key；空则用 lock:{name}
    run_on_start: bool = False,  # True 启动后立刻跑一轮（一般仅测试）
    tick_timeout_sec: float | None = None,  # 单次 tick 上限；None 不限制（防 IO 挂死循环）
    lock_ttl_sec: int | None = None,  # 执行锁租约；None = max(间隔, 10s) 下限
) -> JobRunner:
    """创建主备周期任务，返回可 start/stop 的 JobRunner。

    工号用 ``ctx.instance_id``，Redis 用 ``ctx.require_redis()``。
    服务框架内优先 ``ctx.create_single_leader_job(...)``。

    ``lock_ttl_sec`` 缺省取 ``max(interval_sec, _LOCK_TTL_FLOOR_SEC)``——锁在
    tick 期间由后台续期、每拍结束释放，租约只决定崩溃接管的时延下界；取等于
    调度间隔会让 1Hz 任务在高延迟 Redis 上续期赶不上过期（见常量注释）。
    """
    redis: Any = ctx.require_redis()
    instance_id = ctx.instance_id
    interval = max(int(interval_sec), 1)
    gather = max(float(gather_window_sec), 0.0)
    lock_ttl = (
        max(int(lock_ttl_sec), 1) if lock_ttl_sec is not None
        else max(interval, _LOCK_TTL_FLOOR_SEC)
    )
    # 元数据 TTL 盖住集合窗口，避免 candidates 在抽签前过期
    meta_ttl = max(_META_TTL_FLOOR_SEC, int(math.ceil(gather)) + 2)
    key = (lock_key or f"lock:{name}").rstrip(":")
    clock = RedisAlignedClock(redis)
    return JobRunner(
        name=name,
        schedule=IntervalSchedule(interval),
        coordinator=SingleLeaderCoordinator(
            redis,
            lock_key=key,
            lock_ttl_sec=lock_ttl,
            token_prefix=name,
            instance_id=instance_id,
            gather_window_sec=gather,
            meta_ttl_sec=meta_ttl,
            clock=clock,
        ),
        on_tick=on_tick,
        instance_id=instance_id,
        redis=redis,
        clock=clock,
        gather_window_sec=gather,
        run_on_start=run_on_start,
        tick_timeout_sec=tick_timeout_sec,
    )
