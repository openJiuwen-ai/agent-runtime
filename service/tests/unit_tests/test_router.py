# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""MessageRouter 单测：注册/派发/中间件链/唯一性/流式（设计 §6.2、§6.3）。"""
import asyncio

import pytest
from pydantic import BaseModel

from openjiuwen_runtime.service.context.system_context import SystemContext
from openjiuwen_runtime.service.envelope import Envelope, Metadata, ResponseEnvelope
from openjiuwen_runtime.service.errors import ValidationError
from openjiuwen_runtime.service.routing.router import MessageRouter
from openjiuwen_runtime.service.routing.result import UnaryResult, StreamResult


def _env(t="echo", rid="r1", raw=None):
    return Envelope(type=t, metadata=Metadata(request_id=rid), rawdata=raw or {})


@pytest.mark.unit
async def test_unary_dispatch_wraps_dict_into_response_envelope():
    router = MessageRouter()

    @router.handle("echo")
    async def echo(ctx, env):
        return {"echo": env.rawdata["m"]}

    res = await router.dispatch(_env(raw={"m": "hi"}), object())
    assert isinstance(res, UnaryResult)
    assert res.response.ok is True
    assert res.response.type == "echo"
    assert res.response.rawdata == {"echo": "hi"}
    assert res.response.metadata.request_id == "r1"      # 回填 request_id


@pytest.mark.unit
async def test_unary_dispatch_passes_through_response_envelope():
    router = MessageRouter()

    @router.handle("echo")
    async def echo(ctx, env):
        return ResponseEnvelope(type="echo", metadata=env.metadata, rawdata={"k": 1}, ok=True)

    res = await router.dispatch(_env(), object())
    assert res.response.rawdata == {"k": 1}
    assert res.response.ok is True


@pytest.mark.unit
async def test_unknown_type_returns_not_found_error_result():
    router = MessageRouter()
    res = await router.dispatch(_env(t="missing"), object())
    assert isinstance(res, UnaryResult)
    assert res.response.ok is False
    assert res.response.error_code == "not_found"


@pytest.mark.unit
async def test_handler_exception_normalized_to_error_envelope():
    router = MessageRouter()

    @router.handle("echo")
    async def echo(ctx, env):
        raise ValidationError("bad input")

    res = await router.dispatch(_env(), object())
    assert res.response.ok is False
    assert res.response.error_code == "validation"
    assert "bad input" in res.response.error_message

    @router.handle("boom")
    async def boom(ctx, env):
        raise RuntimeError("unexpected")

    res2 = await router.dispatch(_env(t="boom"), object())
    assert res2.response.ok is False
    assert res2.response.error_code == "internal"        # 非 FrameworkError 归一 internal


@pytest.mark.unit
async def test_middleware_onion_order():
    router = MessageRouter()
    calls: list[str] = []

    async def mw_a(ctx, env, nxt):
        calls.append("a-before")
        res = await nxt(ctx, env)
        calls.append("a-after")
        return res

    async def mw_b(ctx, env, nxt):
        calls.append("b-before")
        res = await nxt(ctx, env)
        calls.append("b-after")
        return res

    router.use(mw_a)
    router.use(mw_b)

    @router.handle("echo")
    async def echo(ctx, env):
        calls.append("handler")
        return {"ok": 1}

    await router.dispatch(_env(), object())
    # 先注册为外层：a-before → b-before → handler → b-after → a-after
    assert calls == ["a-before", "b-before", "handler", "b-after", "a-after"]


@pytest.mark.unit
async def test_duplicate_type_raises():
    router = MessageRouter()

    @router.handle("echo")
    async def echo(ctx, env):
        return {}

    with pytest.raises(Exception):
        @router.handle("echo")
        async def echo2(ctx, env):
            return {}


@pytest.mark.unit
async def test_stream_xor_unary_conflict():
    router = MessageRouter()

    @router.handle("x")
    async def unary(ctx, env):
        return {}

    with pytest.raises(Exception):
        @router.stream("x")
        async def stream(ctx, env):
            yield {}


@pytest.mark.unit
async def test_stream_dispatch_assigns_sequence_and_final():
    router = MessageRouter()

    @router.stream("gen")
    async def gen(ctx, env):
        yield {"n": 1}
        yield {"n": 2}

    res = await router.dispatch(_env(t="gen"), object())
    assert isinstance(res, StreamResult)
    chunks = [c async for c in res.chunks]
    assert len(chunks) == 2
    assert [c.sequence for c in chunks] == [1, 2]
    assert chunks[0].is_final is False and chunks[1].is_final is True
    assert chunks[0].rawdata == {"n": 1}
    assert chunks[1].metadata.request_id == "r1"


class _CreateRequest(BaseModel):
    name: str
    count: int


@pytest.mark.unit
async def test_typed_request_is_validated_once_and_shared_by_context_and_envelope():
    router = MessageRouter()
    env = _env(t="create", raw={"name": "demo", "count": "2"})
    rctx = SystemContext().for_request(env)

    @router.handle("create", request_model=_CreateRequest)
    async def create(ctx, typed_env):
        assert isinstance(typed_env.rawdata, _CreateRequest)
        assert ctx.request is typed_env.rawdata
        return {"name": ctx.request.name, "count": ctx.request.count}

    res = await router.dispatch(env, rctx)

    assert res.response.ok is True
    assert res.response.rawdata == {"name": "demo", "count": 2}
    await rctx.close()


@pytest.mark.unit
async def test_typed_request_validation_error_uses_validation_envelope():
    router = MessageRouter()
    called = False

    @router.handle("create", request_model=_CreateRequest)
    async def create(ctx, env):
        nonlocal called
        called = True
        return {}

    res = await router.dispatch(_env(t="create", raw={"name": "demo"}), object())

    assert called is False
    assert res.response.ok is False
    assert res.response.error_code == "validation"
    assert "count" in res.response.error_message


@pytest.mark.unit
async def test_stream_request_model_is_applied_before_iteration():
    router = MessageRouter()

    @router.stream("create", request_model=_CreateRequest)
    async def create(ctx, env):
        yield {"count": env.rawdata.count}

    res = await router.dispatch(
        _env(t="create", raw={"name": "demo", "count": 3}),
        object(),
    )
    chunks = [chunk async for chunk in res.chunks]

    assert chunks[0].rawdata == {"count": 3}


@pytest.mark.unit
async def test_preinterrupted_request_does_not_call_unary_handler():
    router = MessageRouter()
    ctx = SystemContext().for_request(_env())
    ctx.interrupt("cancelled by caller")
    called = False

    @router.handle("echo")
    async def echo(handler_ctx, env):
        nonlocal called
        called = True
        return {}

    result = await router.dispatch(_env(), ctx)

    assert called is False
    assert result.response.error_code == "interrupted"
    assert result.response.error_message == "cancelled by caller"
    await ctx.close()


@pytest.mark.unit
async def test_unary_handler_uses_remaining_deadline():
    router = MessageRouter()
    ctx = SystemContext(request_timeout_seconds=0.01).for_request(_env())

    @router.handle("echo")
    async def echo(handler_ctx, env):
        await asyncio.sleep(1)
        return {}

    result = await router.dispatch(_env(), ctx)

    assert result.response.ok is False
    assert result.response.error_code == "deadline_exceeded"
    await ctx.close()


@pytest.mark.unit
async def test_stream_deadline_and_close_are_applied_during_iteration():
    router = MessageRouter()
    ctx = SystemContext(request_timeout_seconds=0.02).for_request(_env(t="gen"))
    cleaned: list[str] = []
    ctx.add_cleanup(lambda: cleaned.append("closed"))

    @router.stream("gen")
    async def gen(handler_ctx, env):
        yield {"n": 1}
        await asyncio.sleep(1)
        yield {"n": 2}

    result = await router.dispatch(_env(t="gen"), ctx)
    chunks = [chunk async for chunk in result.chunks]

    assert chunks[0].rawdata == {"n": 1}
    assert chunks[0].is_final is False
    assert chunks[-1].is_final is True
    assert chunks[-1].error_code == "deadline_exceeded"
    assert cleaned == ["closed"]
    await result.aclose()
    assert cleaned == ["closed"]


@pytest.mark.unit
async def test_unconsumed_stream_can_be_closed_explicitly():
    router = MessageRouter()
    ctx = SystemContext().for_request(_env(t="gen"))
    cleaned: list[str] = []
    ctx.add_cleanup(lambda: cleaned.append("closed"))

    @router.stream("gen")
    async def gen(handler_ctx, env):
        yield {"n": 1}

    result = await router.dispatch(_env(t="gen"), ctx)
    await result.aclose()

    assert cleaned == ["closed"]
