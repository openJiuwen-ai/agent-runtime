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
async def test_get_returns_one_item_when_both_queues_ready() -> None:
    """Both queues becoming ready in the same scheduling step must not crash get()."""
    q: PriorityDualAsyncQueues[str] = PriorityDualAsyncQueues(10, 10)
    get_task = asyncio.create_task(q.get())
    await asyncio.sleep(0)
    await asyncio.gather(q.put_user("u1"), q.put_system("s1"))
    first = await get_task
    assert first == "s1"
    assert await q.get() == "u1"
    q.mark_closed()


@pytest.mark.asyncio
async def test_maxsize() -> None:
    """满队列时 put 阻塞，asyncio 队列的 put 不抛 QueueFull，故用超时代替。"""
    q: PriorityDualAsyncQueues[int] = PriorityDualAsyncQueues(1, 1)
    await q.put_system(1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.put_system(2), timeout=0.1)
    q.mark_closed()
