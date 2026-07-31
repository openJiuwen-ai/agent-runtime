# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""适配器集成测试：REST SSE 流式 / 错误状态、WS 流式、REST 幂等。覆盖 echo 验收之外的路径。"""
import json

import fakeredis.aioredis
import httpx
import pytest
from starlette.testclient import TestClient

from openjiuwen_runtime.service import App, Envelope, SystemContext, ValidationError, idempotency_guard


def _env(t: str, rid="r1", raw=None):
    return {"type": t, "metadata": {"request_id": rid}, "rawdata": raw or {}}


def _ctx_factory():
    return SystemContext(redis=fakeredis.aioredis.FakeRedis())


def _build_stream_app():
    app = App(_ctx_factory)

    @app.stream("count")
    async def count(ctx, env: Envelope):
        for i in range(3):
            yield {"n": i}

    return app


def _build_error_app():
    app = App(_ctx_factory)

    @app.handle("boom")
    async def boom(ctx, env: Envelope):
        raise ValidationError("nope")

    return app


def _build_idem_app():
    app = App(_ctx_factory)
    app.use(idempotency_guard(window=60, mode="reject"))

    @app.handle("send")
    async def send(ctx, env: Envelope):
        return {"sent": True}

    return app


# ---------- REST 流式（SSE）----------
@pytest.mark.system
async def test_rest_stream_returns_sse_chunks():
    app = _build_stream_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.asgi),
                                 base_url="http://test") as client:
        r = await client.post("/api/count", json=_env("count"))
        assert r.status_code == 200
        data_lines = [ln[len("data: "):] for ln in r.text.splitlines() if ln.startswith("data: ")]
        chunks = [json.loads(line) for line in data_lines]
        assert [c["sequence"] for c in chunks] == [1, 2, 3]
        assert chunks[0]["rawdata"] == {"n": 0}
        assert chunks[-1]["is_final"] is True


# ---------- REST 错误状态 ----------
@pytest.mark.system
async def test_rest_error_returns_non_200_with_error_envelope():
    app = _build_error_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.asgi),
                                 base_url="http://test") as client:
        r = await client.post("/api/boom", json=_env("boom"))
        body = r.json()
        assert r.status_code == 400                  # validation → 400
        assert body["ok"] is False
        assert body["error_code"] == "validation"
        assert "nope" in body["error_message"]


# ---------- WebSocket 流式 ----------
@pytest.mark.system
def test_ws_stream_returns_chunked_frames():
    app = _build_stream_app()
    client = TestClient(app.asgi)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps(_env("count")))
        frames = [json.loads(ws.receive_text()) for _ in range(3)]
    assert [f["sequence"] for f in frames] == [1, 2, 3]
    assert frames[-1]["is_final"] is True
    assert frames[0]["rawdata"] == {"n": 0}


# ---------- REST 幂等（reject）----------
@pytest.mark.system
async def test_rest_idempotency_rejects_duplicate():
    app = _build_idem_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.asgi),
                                 base_url="http://test") as client:
        env = _env("send", rid="dup-1")
        r1 = await client.post("/api/send", json=env)
        r2 = await client.post("/api/send", json=env)
        assert r1.status_code == 200 and r1.json()["ok"] is True
        assert r1.json()["rawdata"] == {"sent": True}
        assert r2.status_code == 409                  # idempotent → 409
        assert r2.json()["error_code"] == "idempotent"
