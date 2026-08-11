# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""第一验收用例（system）。验收：
  1) 路由层：两个 SystemContext(副本) 共享 redis，idx 全局递增（不各自从 1 开始）。
  2) REST：POST /api/echo，body=完整 Envelope，返回 ResponseEnvelope，idx 递增。
  3) WebSocket：/ws 帧=Envelope，回帧含 idx。
依赖：httpx、fakeredis。"""
import json

import fakeredis.aioredis
import httpx
import pytest

from openjiuwen_runtime.service import App, Envelope, Metadata, SystemContext


def build_echo_app(ctx_factory) -> App:
    app = App(ctx_factory)

    @app.handle("echo")
    async def echo(ctx, env: Envelope):
        idx = await ctx.kv.incr("echo:idx")          # 分布式原子计数
        return {"echo": env.rawdata.get("message", ""), "idx": idx}

    return app


def _envelope(request_id: str, message: str) -> dict:
    return {"type": "echo", "metadata": {"request_id": request_id},
            "rawdata": {"message": message}}


# ---------- 1) 核心分布式验收：双副本共享 redis，idx 全局递增 ----------
@pytest.mark.system
async def test_global_idx_across_two_replicas():
    shared = fakeredis.aioredis.FakeServer()                  # 模拟两副本共享的 redis

    ctx_a = SystemContext(redis=fakeredis.aioredis.FakeRedis(server=shared))
    ctx_b = SystemContext(redis=fakeredis.aioredis.FakeRedis(server=shared))
    await ctx_a.start()
    await ctx_b.start()

    app = build_echo_app(lambda: None)                        # 直调派发，不触发 lifespan
    try:
        for i, ctx in enumerate([ctx_a, ctx_b, ctx_a, ctx_b]):   # 交替打两个副本
            env = Envelope(type="echo",
                           metadata=Metadata(request_id=f"r{i}"),
                           rawdata={"message": "hi"})
            rctx = ctx.for_request(env.metadata)              # 派生请求上下文
            try:
                res = await app.dispatch(env, rctx)           # 非流式 → UnaryResult(ResponseEnvelope)
                assert res.response.rawdata == {"echo": "hi", "idx": i + 1}   # 1,2,3,4 全局递增
            finally:
                await rctx.close()
    finally:
        await ctx_a.stop()
        await ctx_b.stop()


# ---------- 2) REST 适配器 ----------
@pytest.mark.system
async def test_rest_echo_returns_incrementing_idx():
    app = build_echo_app(lambda: SystemContext(redis=fakeredis.aioredis.FakeRedis()))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.asgi),
                                 base_url="http://test") as client:
        r1 = await client.post("/api/echo", json=_envelope("r1", "hi"))
        r2 = await client.post("/api/echo", json=_envelope("r2", "yo"))
        body1, body2 = r1.json(), r2.json()
        assert r1.status_code == 200 and body1["ok"] is True
        assert body1["rawdata"] == {"echo": "hi", "idx": 1}
        assert body2["rawdata"] == {"echo": "yo", "idx": 2}


# ---------- 3) WebSocket 适配器 ----------
@pytest.mark.system
def test_ws_echo_returns_idx():
    from starlette.testclient import TestClient

    app = build_echo_app(lambda: SystemContext(redis=fakeredis.aioredis.FakeRedis()))
    client = TestClient(app.asgi)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps(_envelope("r1", "hi")))
        data = json.loads(ws.receive_text())
        assert data["ok"] is True
        assert data["rawdata"] == {"echo": "hi", "idx": 1}


@pytest.mark.system
async def test_rest_handler_can_use_raw_async_redis_commands():
    redis = fakeredis.aioredis.FakeRedis()
    app = App(lambda: SystemContext(redis=redis))

    @app.handle("redis.profile")
    async def save_profile(ctx, env: Envelope):
        key = f"user:{ctx.user_id}"
        await ctx.redis.hset(key, mapping=env.rawdata)
        name = await ctx.redis.hget(key, "name")
        return {"name": name.decode("utf-8")}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app.asgi),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/redis.profile",
            json={
                "type": "redis.profile",
                "metadata": {"request_id": "redis-1", "user_id": "user-1"},
                "rawdata": {"name": "Alice"},
            },
        )

    assert response.status_code == 200
    assert response.json()["rawdata"] == {"name": "Alice"}
