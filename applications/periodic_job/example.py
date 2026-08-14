# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""主备定时任务 —— 怎么调用。

redis 由调用方注入（生产用真 Redis；测试可注入 FakeRedis）。
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from openjiuwen_runtime.foundation.periodic import create_single_leader_job


def _instance_id() -> str:
    """本机工号：集群里每台机器必须不一样，否则抽签分不清谁是谁。"""
    return socket.gethostname()


async def example_create_single_leader_job(redis: Any) -> None:
    async def on_tick() -> None:
        # 到点干活；需要时间就自己 time.time()
        print("[demo] tick")

    job = create_single_leader_job(
        redis,  # 【必填】Redis（报名 / 抽签 / 执行锁）
        name="demo",  # 【必填】任务名；不传 lock_key 时 → 锁名 lock:demo
        on_tick=on_tick,  # 【必填】到点回调（无参）
        instance_id=_instance_id(),  # 【必填】本机实例 ID
        # interval_sec=1,            # 【选填】默认 1；执行锁 TTL = 此值
        # gather_window_sec=0.08,    # 【选填】默认 0.08；开火前提前醒来报名
        # lock_key="",               # 【选填】空则 lock:{name}；一般不用改
        # run_on_start=False,        # 【选填】True=启动立刻跑一轮（多用于测试）
    )
    await job.start()
    try:
        await asyncio.sleep(5)  # 演示跑几秒；生产里挂在服务生命周期上
    finally:
        await job.stop()


if __name__ == "__main__":
    # 注入 redis 后跑：
    #   import redis.asyncio as redis
    #   r = redis.from_url("redis://127.0.0.1:6379/0")
    #   asyncio.run(example_create_single_leader_job(r))
    raise SystemExit(
        "请注入 redis 后调用：asyncio.run(example_create_single_leader_job(your_redis))"
    )
