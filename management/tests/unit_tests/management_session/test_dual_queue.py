# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import asyncio

import pytest

from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues


@pytest.mark.asyncio
async def test_system_priority_over_user() -> None:
    q: PriorityDualAsyncQueues[str] = PriorityDualAsyncQueues(10, 10)
    await q.put_user("u1")
    await q.put_system("s1")
    g1 = await q.get()
    g2 = await q.get()
    assert g1 == "s1" and g2 == "u1"
    q.mark_closed()


@pytest.mark.asyncio
async def test_maxsize() -> None:
    """满队列时 put 阻塞，asyncio 队列的 put 不抛 QueueFull，故用超时代替。"""
    q: PriorityDualAsyncQueues[int] = PriorityDualAsyncQueues(1, 1)
    await q.put_system(1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.put_system(2), timeout=0.1)
    q.mark_closed()
