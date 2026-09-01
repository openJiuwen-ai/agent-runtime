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


@pytest.mark.asyncio
async def test_get_both_sides_ready_same_window_no_loss() -> None:
    """回归(生产事故)：消费方阻塞等待期间，用户/系统项在同一事件循环窗口入队。

    复现手段：先让 get() 挂进 asyncio.wait 并注册两个 getter；随后 put_user 与
    put_system 之间不让出控制权（put 非满不真正挂起），两个 getter 在消费方恢复
    前都完成——旧实现 ``len(done)!=1`` 误抛 "is closed"，两条消息被取走后直接丢失。

    修复后断言：系统项当次交付（优先级不变），用户项由暂存于下一次 get() 交付，
    不丢失、用户侧顺序保持；user_qsize 计入暂存。
    """
    q: PriorityDualAsyncQueues[str] = PriorityDualAsyncQueues(10, 10)
    consumer = asyncio.create_task(q.get())
    # 让 consumer 及两个 getter task 均挂稳（进入 asyncio.wait 并注册 getter）
    for _ in range(4):
        await asyncio.sleep(0)

    await q.put_user("u1")
    await q.put_system("s1")

    first = await asyncio.wait_for(consumer, timeout=1.0)
    assert first == "s1", "系统项应优先交付"
    assert q.user_qsize() == 1, "暂存的用户项应计入 user_qsize"

    second = await asyncio.wait_for(q.get(), timeout=1.0)
    assert second == "u1", "用户项不得丢失, 应由暂存在下次 get() 交付"
    assert q.user_qsize() == 0
    q.mark_closed()


@pytest.mark.asyncio
async def test_rescued_user_item_delivered_before_new_messages() -> None:
    """竞态暂存的用户项应先于后续新入队消息交付（保持用户侧 FIFO）。"""
    q: PriorityDualAsyncQueues[str] = PriorityDualAsyncQueues(10, 10)
    consumer = asyncio.create_task(q.get())
    for _ in range(4):
        await asyncio.sleep(0)

    await q.put_user("u1")
    await q.put_system("s1")
    assert await asyncio.wait_for(consumer, timeout=1.0) == "s1"

    # 暂存未交付前又来了一条用户消息与一条系统消息
    await q.put_user("u2")
    await q.put_system("s2")

    # 暂存的 u1 最先交付, 随后 s2(系统优先), 最后 u2
    assert await asyncio.wait_for(q.get(), timeout=1.0) == "u1"
    assert await asyncio.wait_for(q.get(), timeout=1.0) == "s2"
    assert await asyncio.wait_for(q.get(), timeout=1.0) == "u2"
    q.mark_closed()
