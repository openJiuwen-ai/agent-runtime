import asyncio
import json

import pytest
from openjiuwen_runtime.foundation.security.link_profile import LinkProfileError
from openjiuwen_runtime.foundation.security.link_stream_guard import LinkBindingMiddleware

SCOPE = {"type": "http", "path": "/stream", "headers": []}


async def receive():
    await asyncio.Future()


def test_rejected_before_application():
    async def run():
        messages = []

        async def app(*args):
            pytest.fail("unauthorized application called")

        def authorize(request):
            raise LinkProfileError("revoked")

        async def send(message):
            messages.append(message)

        await LinkBindingMiddleware(app, authorize=authorize)(SCOPE, receive, send)
        assert messages[0]["status"] == 403
        assert json.loads(messages[1]["body"])["error"]["code"] == "LINK_BINDING_MISMATCH"

    asyncio.run(run())


@pytest.mark.parametrize("idle", [True, False])
def test_revocation_stops_response_and_runs_cleanup(idle):
    async def run():
        messages = []
        state = {"valid": True, "cleaned": False}

        def authorize(request):
            if not state["valid"]:
                raise LinkProfileError("revoked")

        async def app(scope, recv, send):
            try:
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"before", "more_body": True})
                state["valid"] = False
                if idle:
                    await asyncio.Future()
                else:
                    await send({"type": "http.response.body", "body": b"must-not-leak", "more_body": True})
            finally:
                state["cleaned"] = True

        async def send(message):
            messages.append(message)

        middleware = LinkBindingMiddleware(app, authorize=authorize, check_interval=0.01)
        await asyncio.wait_for(middleware(SCOPE, receive, send), 0.5)
        assert state["cleaned"]
        assert [m.get("body") for m in messages if m["type"] == "http.response.body"] == [b"before", b""]
        assert messages[-1]["more_body"] is False

    asyncio.run(run())


def test_normal_response_and_non_http_passthrough():
    async def run():
        called = []
        messages = []

        def authorize(request):
            assert request.scope["type"] == "http"
            called.append(request.headers)

        async def app(scope, recv, send):
            if scope["type"] == "lifespan":
                return
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def send(message):
            messages.append(message)

        middleware = LinkBindingMiddleware(app, authorize=authorize)
        await middleware(SCOPE, receive, send)
        await middleware({"type": "lifespan"}, receive, send)
        assert len(called) == 3
        assert messages[-1]["body"] == b"ok"

    asyncio.run(run())


def test_application_error_is_not_hidden():
    async def run():
        async def app(*args):
            raise RuntimeError("business failure")

        async def send(message):
            pytest.fail("must not turn business error into success")

        middleware = LinkBindingMiddleware(app, authorize=lambda request: None)
        with pytest.raises(RuntimeError, match="business failure"):
            await middleware(SCOPE, receive, send)

    asyncio.run(run())


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan")])
def test_invalid_poll_interval(interval):
    with pytest.raises(ValueError):
        LinkBindingMiddleware(None, authorize=None, check_interval=interval)


def test_partial_fixed_length_response_is_aborted_not_completed():
    async def run():
        active, messages = True, []

        def authorize(request):
            if not active:
                raise LinkProfileError("revoked")

        async def app(scope, receive_request, send):
            nonlocal active
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"10")]})
            await send({"type": "http.response.body", "body": b"a", "more_body": True})
            active = False
            await send({"type": "http.response.body", "body": b"secret123"})

        async def send(message):
            messages.append(message)

        with pytest.raises(LinkProfileError, match="during response"):
            await asyncio.wait_for(LinkBindingMiddleware(app, authorize=authorize)(SCOPE, receive, send), 1)
        assert len(messages) == 2
        assert messages[-1]["body"] == b"a"

    asyncio.run(run())
