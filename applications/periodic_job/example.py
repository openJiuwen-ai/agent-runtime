# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""主备定时任务 —— 怎么调用。

用 ``SystemContext.create_single_leader_job``：redis / 工号都从上下文取。
（不传 instance_id 时默认是「主机名+随机号」。）
"""

from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.service import SystemContext

logger = get_logger(__name__)


async def example_create_single_leader_job(redis: Any) -> None:
    async def on_tick() -> None:
        # 到点干活；调度时钟是 Redis TIME，业务若要时间自己再读
        logger.info("[demo] tick")

    # 不传 instance_id：上下文自己生成唯一工号
    ctx = SystemContext(redis=redis)
    job = ctx.create_single_leader_job(
        name="demo",  # 【必填】任务名；不传 lock_key 时 → 锁名 lock:demo
        on_tick=on_tick,  # 【必填】到点回调（无参）
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
    import redis.asyncio as redis

    async def _main() -> None:
        client = redis.from_url("redis://127.0.0.1:6379/0")
        try:
            await example_create_single_leader_job(client)
        finally:
            await client.aclose()

    asyncio.run(_main())
