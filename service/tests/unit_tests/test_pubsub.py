# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""PubSub 原语单测（设计 §9.5）：Redis Pub/Sub 事件总线。"""
import asyncio

import fakeredis.aioredis
import pytest

from openjiuwen_runtime.service.context.primitives.pubsub import PubSub


@pytest.mark.unit
async def test_publish_subscribe_roundtrip():
    shared = fakeredis.aioredis.FakeServer()
    pub = PubSub(fakeredis.aioredis.FakeRedis(server=shared), prefix="svc")
    sub = PubSub(fakeredis.aioredis.FakeRedis(server=shared), prefix="svc")

    received: list[dict] = []

    async def reader():
        async for msg in sub.subscribe("events"):
            received.append(msg)
            if len(received) >= 2:
                break

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.1)                       # 等订阅生效
    n = await pub.publish("events", {"a": 1})
    assert n == 1                                  # 1 个订阅者收到
    await pub.publish("events", {"b": 2})
    await asyncio.wait_for(task, timeout=2)
    assert received == [{"a": 1}, {"b": 2}]


@pytest.mark.unit
async def test_publish_with_no_subscribers_returns_zero():
    pub = PubSub(fakeredis.aioredis.FakeRedis(), prefix="svc")
    assert await pub.publish("lonely", {"x": 1}) == 0
