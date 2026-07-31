# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""StreamQueue 原语单测（设计 §9.4）：XADD/XREADGROUP/XACK，at-least-once。"""
import fakeredis.aioredis
import pytest

from openjiuwen_runtime.service.context.primitives.stream_queue import StreamQueue


@pytest.mark.unit
async def test_enqueue_consume_ack():
    sq = StreamQueue(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await sq.enqueue("tasks", {"x": 1})
    got = []
    async for item in sq.consume("g1", "c1", stream="tasks", block=0):
        got.append(item.data)
        await item.ack()
        break
    assert got == [{"x": 1}]


@pytest.mark.unit
async def test_unacked_message_stays_pending():
    # at-least-once：不 ack → 消息留在消费组 PEL（不丢，可被重新处理/重投）
    # 注：fakeredis 的 XREADGROUP 不能二次重投 pending，故用 xpending 验证「未丢」契约。
    sq = StreamQueue(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await sq.enqueue("tasks", {"x": 1})
    async for item in sq.consume("g1", "c1", stream="tasks", block=0):
        break                          # 不 ack
    pending = await sq._redis.xpending("svc:tasks", "g1")
    assert pending["pending"] == 1     # 仍挂账 → at-least-once


@pytest.mark.unit
async def test_group_does_not_double_deliver_same_message():
    # 同一消费组内：一条消息只被一个 consumer 取走（不重复投递给同组）
    sq = StreamQueue(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await sq.enqueue("tasks", {"x": 1})

    taken = []
    async for item in sq.consume("g1", "c1", stream="tasks", block=0, count=1):
        taken.append(item.data)
        await item.ack()
        break

    # c1 已 ack；c2（同组）随后非阻塞读取应拿不到该消息（生成器空即停止）
    async for item in sq.consume("g1", "c2", stream="tasks", block=0, count=1):
        pytest.fail("同组已 ack 的消息不应再投给 c2")
    assert taken == [{"x": 1}]


@pytest.mark.unit
async def test_ack_makes_message_not_redelivered():
    sq = StreamQueue(fakeredis.aioredis.FakeRedis(), prefix="svc")
    await sq.enqueue("tasks", {"x": 1})
    async for item in sq.consume("g1", "c1", stream="tasks", block=0):
        await item.ack()
        break
    # 已 ack → 同 consumer 再读 own-pending 为空，新读无消息（不重投）
    redelivered = []
    async for item in sq.consume("g1", "c1", stream="tasks", block=0):
        redelivered.append(item.data)
        break
    assert redelivered == []
