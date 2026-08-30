# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""适配器集成测试：REST SSE 流式 / 错误状态、WS 流式、REST 幂等。覆盖 echo 验收之外的路径。"""
import json
import asyncio
import logging

import fakeredis.aioredis
import httpx
import pytest
from pydantic import BaseModel
from starlette.testclient import TestClient

from openjiuwen_runtime.service import (
    App,
    Envelope,
    SystemContext,
    TypedAppContext,
    ValidationError,
    idempotency_guard,
)


def _env(t: str, rid="r1", raw=None):
    return {"type": t, "metadata": {"request_id": rid}, "rawdata": raw or {}}


def _ctx_factory():
    return SystemContext(redis=fakeredis.aioredis.FakeRedis())


class _TrackingSystemContext(SystemContext):
    def __init__(self, closed: list[str]) -> None:
        super().__init__(redis=fakeredis.aioredis.FakeRedis())
        self._closed_requests = closed

    def for_request(self, request):
        ctx = super().for_request(request)
        ctx.add_cleanup(lambda: self._closed_requests.append(ctx.request_id))
        return ctx


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


class _TypedInput(BaseModel):
    value: int


def _build_typed_app():
    app = App(_ctx_factory)

    @app.handle("typed", request_model=_TypedInput)
    async def typed(
        ctx: TypedAppContext[_TypedInput],
        env: Envelope[_TypedInput],
    ):
        return {
            "value": ctx.request.value,
            "same_request": ctx.request is env.rawdata,
            "same_envelope": ctx.envelope is env,
        }

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


# ---------- REST 前置校验失败（422，未进 router）----------
@pytest.mark.system
async def test_rest_envelope_validation_422_logs_and_keeps_default_body(caplog):
    """信封体模型被 FastAPI 前置拒绝 → 422 默认响应形状不变 + WARNING 留痕。

    该层失败不进 router（无请求汇总行/上下文尾巴），adapter 的 WARNING 是
    唯一日志证据；request_id 尽力从原始 body 抢救。
    """
    adapter_logger = "openjiuwen_runtime.service.server.rest_adapter"
    app = _build_stream_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.asgi),
                                 base_url="http://test") as client:
        with caplog.at_level(logging.WARNING, logger=adapter_logger):
            r = await client.post("/api/count", json={"type": "count"})
        assert r.status_code == 422
        assert "detail" in r.json()          # FastAPI 默认响应形状不变
        warned = [rec for rec in caplog.records
                  if "request validation failed" in rec.getMessage()]
        assert len(warned) == 1
        assert "path=/api/count" in warned[0].getMessage()
        assert "request_id=-" in warned[0].getMessage()   # 无从抢救

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=adapter_logger):
            # metadata 合法但缺 rawdata → request_id 应被抢救出来
            r2 = await client.post("/api/count", json={
                "type": "count", "metadata": {"request_id": "rid-422"},
            })
        assert r2.status_code == 422
        warned2 = [rec for rec in caplog.records
                   if "request validation failed" in rec.getMessage()]
        assert len(warned2) == 1
        assert "request_id=rid-422" in warned2[0].getMessage()


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


@pytest.mark.system
async def test_rest_typed_request_context_and_validation():
    app = _build_typed_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app.asgi),
        base_url="http://test",
    ) as client:
        valid = await client.post("/api/typed", json=_env("typed", raw={"value": "7"}))
        invalid = await client.post("/api/typed", json=_env("typed", raw={}))

    assert valid.status_code == 200
    assert valid.json()["rawdata"] == {
        "value": 7,
        "same_request": True,
        "same_envelope": True,
    }
    assert invalid.status_code == 400
    assert invalid.json()["error_code"] == "validation"


@pytest.mark.system
def test_ws_typed_request_context():
    app = _build_typed_app()
    client = TestClient(app.asgi)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps(_env("typed", raw={"value": "9"})))
        response = json.loads(ws.receive_text())

    assert response["ok"] is True
    assert response["rawdata"] == {
        "value": 9,
        "same_request": True,
        "same_envelope": True,
    }


@pytest.mark.system
async def test_rest_closes_unary_validation_error_and_stream_contexts():
    closed: list[str] = []
    app = App(lambda: _TrackingSystemContext(closed))

    @app.handle("ok")
    async def ok(ctx, env):
        return {"ok": True}

    @app.handle("typed-close", request_model=_TypedInput)
    async def typed_close(ctx, env):
        return {}

    @app.stream("stream-close")
    async def stream_close(ctx, env):
        yield {"done": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app.asgi), base_url="http://test"
    ) as client:
        unary = await client.post("/api/ok", json=_env("ok", rid="unary"))
        invalid = await client.post(
            "/api/typed-close", json=_env("typed-close", rid="invalid")
        )
        stream = await client.post(
            "/api/stream-close", json=_env("stream-close", rid="stream")
        )

    assert unary.status_code == 200
    assert invalid.status_code == 400
    assert stream.status_code == 200
    assert closed == ["unary", "invalid", "stream"]


@pytest.mark.system
def test_websocket_closes_one_context_per_envelope():
    closed: list[str] = []
    app = App(lambda: _TrackingSystemContext(closed))

    @app.handle("ok")
    async def ok(ctx, env):
        return {"ok": True}

    @app.stream("stream-close")
    async def stream_close(ctx, env):
        yield {"done": True}

    client = TestClient(app.asgi)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps(_env("ok", rid="ws-unary")))
        ws.receive_text()
        ws.send_text(json.dumps(_env("stream-close", rid="ws-stream")))
        ws.receive_text()

    assert closed == ["ws-unary", "ws-stream"]


@pytest.mark.system
async def test_rest_task_cancellation_closes_context():
    closed: list[str] = []
    started = asyncio.Event()
    app = App(lambda: _TrackingSystemContext(closed))

    @app.handle("slow")
    async def slow(ctx, env):
        started.set()
        await asyncio.Event().wait()
        return {}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app.asgi), base_url="http://test"
    ) as client:
        request_task = asyncio.create_task(
            client.post("/api/slow", json=_env("slow", rid="cancelled"))
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

    assert closed == ["cancelled"]
