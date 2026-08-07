# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Handler object, registry, and App composition tests."""

import pytest
from pydantic import BaseModel

from openjiuwen_runtime.service import (
    App,
    Envelope,
    HandlerRegistry,
    HandlerSpec,
    MessageHandler,
    Metadata,
    OAuth2AccessControl,
    StreamMessageHandler,
    SystemContext,
)
from openjiuwen_runtime.service.routing.result import StreamResult, UnaryResult


class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    echo: str


class EchoHandler(MessageHandler):
    spec = HandlerSpec(
        msg_type="echo.object",
        request_model=EchoInput,
        response_model=EchoOutput,
    )

    async def handle(self, ctx, env):
        return {"echo": env.rawdata.text}


class CountHandler(StreamMessageHandler):
    spec = HandlerSpec(msg_type="count.object")

    async def handle_stream(self, ctx, env):
        for number in range(2):
            yield {"number": number}


def _env(msg_type: str, rawdata=None) -> Envelope:
    return Envelope(
        type=msg_type,
        metadata=Metadata(request_id="request-1"),
        rawdata=rawdata or {},
    )


@pytest.mark.unit
async def test_app_register_and_register_all_dispatch_objects():
    app = App(lambda: SystemContext(), enable_rest=False, enable_ws=False)
    app.register(EchoHandler())
    app.register_all([CountHandler()])
    ctx = SystemContext().for_request(Metadata(request_id="request-1"))

    unary = await app.dispatch(_env("echo.object", {"text": "hi"}), ctx)
    assert isinstance(unary, UnaryResult)
    assert unary.response.rawdata == {"echo": "hi"}

    stream = await app.dispatch(_env("count.object"), ctx)
    assert isinstance(stream, StreamResult)
    chunks = [chunk async for chunk in stream.chunks]
    assert [chunk.rawdata for chunk in chunks] == [{"number": 0}, {"number": 1}]
    assert chunks[-1].is_final is True


@pytest.mark.unit
async def test_app_include_registry_and_decorator_share_one_contract():
    registry = HandlerRegistry()

    @registry.handle("module.echo", request_model=EchoInput)
    async def module_echo(ctx, env):
        return {"echo": env.rawdata.text}

    app = App(lambda: SystemContext(), enable_rest=False, enable_ws=False)
    app.include(registry)

    @app.handle("decorator.echo", request_model=EchoInput)
    async def decorator_echo(ctx, env):
        return {"echo": env.rawdata.text}

    ctx = SystemContext().for_request(Metadata(request_id="request-1"))
    module_result = await app.dispatch(_env("module.echo", {"text": "module"}), ctx)
    decorator_result = await app.dispatch(
        _env("decorator.echo", {"text": "decorator"}), ctx
    )
    assert module_result.response.rawdata == {"echo": "module"}
    assert decorator_result.response.rawdata == {"echo": "decorator"}


@pytest.mark.unit
async def test_request_model_validation_applies_to_direct_dispatch():
    app = App(lambda: SystemContext(), enable_rest=False, enable_ws=False)
    app.register(EchoHandler())
    ctx = SystemContext().for_request(Metadata(request_id="request-1"))
    result = await app.dispatch(_env("echo.object", {"text": 123}), ctx)
    assert result.response.ok is False
    assert result.response.error_code == "validation"


@pytest.mark.unit
def test_duplicate_handler_is_rejected():
    registry = HandlerRegistry().register(EchoHandler())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoHandler())


@pytest.mark.unit
def test_sync_unary_handler_is_rejected_at_registration():
    class SyncHandler(MessageHandler):
        spec = HandlerSpec(msg_type="sync")

        def handle(self, ctx, env):
            return {}

    with pytest.raises(TypeError, match="async def"):
        HandlerRegistry().register(SyncHandler())


@pytest.mark.unit
def test_non_generator_stream_handler_is_rejected_at_registration():
    class InvalidStreamHandler(StreamMessageHandler):
        spec = HandlerSpec(msg_type="invalid.stream")

        async def handle_stream(self, ctx, env):
            return []

    with pytest.raises(TypeError, match="async generator"):
        HandlerRegistry().register(InvalidStreamHandler())


@pytest.mark.unit
def test_handler_models_must_be_pydantic_models():
    class InvalidModelHandler(MessageHandler):
        spec = HandlerSpec(msg_type="invalid.model", request_model=dict)

        async def handle(self, ctx, env):
            return {}

    with pytest.raises(TypeError, match="Pydantic model"):
        HandlerRegistry().register(InvalidModelHandler())


@pytest.mark.unit
async def test_response_model_is_enforced_during_dispatch():
    class InvalidResponseHandler(MessageHandler):
        spec = HandlerSpec(msg_type="invalid.response", response_model=EchoOutput)

        async def handle(self, ctx, env):
            return {"unexpected": True}

    app = App(lambda: SystemContext(), enable_rest=False, enable_ws=False)
    app.register(InvalidResponseHandler())
    ctx = SystemContext().for_request(Metadata(request_id="request-1"))
    result = await app.dispatch(_env("invalid.response"), ctx)
    assert result.response.ok is False
    assert result.response.error_code == "internal"
    assert "invalid handler response" in result.response.error_message


@pytest.mark.unit
def test_include_requires_handler_module_contract():
    app = App(lambda: SystemContext(), enable_rest=False, enable_ws=False)
    with pytest.raises(TypeError, match=r"handlers\(\)"):
        app.include(object())


@pytest.mark.unit
def test_oauth2_token_validator_must_be_async():
    def sync_validator(token):
        return {"token": token}

    with pytest.raises(TypeError, match="async def"):
        OAuth2AccessControl(token_url="/token", token_validator=sync_validator)


@pytest.mark.unit
def test_oauth2_access_control_supports_authorization_code_scheme():
    async def validator(token):
        return {"token": token}

    control = OAuth2AccessControl(
        token_url="/oauth/token",
        authorization_url="/oauth/authorize",
        token_validator=validator,
        scheme_name="AuthorizationCode",
    )

    flow = control.scheme.model.flows.authorizationCode
    assert flow.authorizationUrl == "/oauth/authorize"
    assert flow.tokenUrl == "/oauth/token"
