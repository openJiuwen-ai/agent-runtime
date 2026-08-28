from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from manager_server.manager_web import _relay_websocket, create_manager_web_app


class _ChunkStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"type":"res","id":"r2","ok":true}\n\n'
        yield b'data: {"type":"event","event":"chat.final"}\n\n'


def _manager_app(tmp_path: Path):
    (tmp_path / "index.html").write_text("manager", encoding="utf-8")
    return create_manager_web_app(
        tmp_path,
        "http://manager-api:8765",
        "http://identity:8770",
        user_web_url="http://user-web:5173",
        gateway_http_url="http://gateway:19002",
        gateway_ws_url="ws://gateway:19000",
    )


@pytest.mark.asyncio
async def test_manager_web_relays_gateway_http_sse_under_dedicated_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _manager_app(tmp_path)
    original_client = httpx.AsyncClient
    captured: dict[str, object] = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/me":
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"user_id": "u2"})
        captured["url"] = str(request.url)
        captured["body"] = await request.aread()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "cache-control": "no-cache"},
            stream=_ChunkStream(),
        )

    def client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(upstream))

    proxy_client = original_client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://manager-web",
        cookies={"openjiuwen_access_token": "valid-token"},
    )
    monkeypatch.setattr("manager_server.manager_web.httpx.AsyncClient", client_factory)

    async with proxy_client:
        response = await proxy_client.post(
            "/gateway-api/v1/chat/completions?mode=work",
            json={"query": "hello", "enable_streaming": True},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.count("data:") == 2
    assert captured["url"] == "http://gateway:19002/api/v1/chat/completions?mode=work"
    assert captured["auth"] == "Bearer valid-token"
    assert b'"query":"hello"' in captured["body"]


@pytest.mark.asyncio
async def test_manager_web_rejects_anonymous_gateway_request(tmp_path: Path) -> None:
    app = _manager_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager-web"
    ) as client:
        response = await client.post(
            "/gateway-api/v1/chat/completions",
            json={"query": "anonymous"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_manager_api_v1_is_not_routed_to_gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _manager_app(tmp_path)
    original_client = httpx.AsyncClient
    captured: dict[str, str] = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"code": 200, "data": {}})

    def client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(upstream))

    monkeypatch.setattr("manager_server.manager_web.httpx.AsyncClient", client_factory)
    async with original_client(
        transport=httpx.ASGITransport(app=app), base_url="http://manager-web"
    ) as client:
        response = await client.get("/api/v1/instances")

    assert response.status_code == 200
    assert captured["url"] == "http://manager-api:8765/api/v1/instances"


@pytest.mark.asyncio
async def test_embedded_user_web_manager_api_uses_explicit_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _manager_app(tmp_path)
    original_client = httpx.AsyncClient
    captured: dict[str, str | None] = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"code": 200, "data": {"gateways": []}},
        )

    def client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(upstream))

    monkeypatch.setattr("manager_server.manager_web.httpx.AsyncClient", client_factory)
    async with original_client(
        transport=httpx.ASGITransport(app=app), base_url="http://manager-web"
    ) as client:
        response = await client.get(
            "/manager-api/v1/user-console/gateways",
            headers={"authorization": "Bearer user-token"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["gateways"] == []
    assert captured == {
        "url": "http://manager-api:8765/api/v1/user-console/gateways",
        "authorization": "Bearer user-token",
    }


@pytest.mark.asyncio
async def test_manager_api_internal_redirect_is_resolved_by_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _manager_app(tmp_path)
    original_client = httpx.AsyncClient
    captured: list[str] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        if request.url.path == "/api/v1/instances":
            return httpx.Response(
                307,
                headers={
                    "location": (
                        "http://manager-api:8765/api/v1/instances/"
                        "?page=1&page_size=50"
                    )
                },
            )
        return httpx.Response(
            200,
            json={"code": 200, "data": {"items": [], "total": 0}},
        )

    def client_factory(*_args, **kwargs) -> httpx.AsyncClient:
        return original_client(
            transport=httpx.MockTransport(upstream),
            follow_redirects=bool(kwargs.get("follow_redirects", False)),
        )

    monkeypatch.setattr("manager_server.manager_web.httpx.AsyncClient", client_factory)
    async with original_client(
        transport=httpx.ASGITransport(app=app), base_url="http://manager-web"
    ) as client:
        response = await client.get("/api/v1/instances?page=1&page_size=50")

    assert response.status_code == 200
    assert response.headers.get("location") is None
    assert response.json()["data"]["items"] == []
    assert captured == [
        "http://manager-api:8765/api/v1/instances?page=1&page_size=50",
        "http://manager-api:8765/api/v1/instances/?page=1&page_size=50",
    ]


@pytest.mark.asyncio
async def test_manager_web_proxies_user_web_as_same_origin_iframe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _manager_app(tmp_path)
    original_client = httpx.AsyncClient
    captured: list[str] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, text="user-web")

    def client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(upstream))

    monkeypatch.setattr("manager_server.manager_web.httpx.AsyncClient", client_factory)
    async with original_client(
        transport=httpx.ASGITransport(app=app), base_url="http://manager-web"
    ) as client:
        iframe = await client.get(
            "/chat/?user_id=u1&bot_id=b1",
            headers={"sec-fetch-dest": "iframe"},
        )
        asset = await client.get("/chat/assets/chat.js")
        standalone = await client.get(
            "/chat/?user_id=u1",
            headers={"sec-fetch-dest": "document"},
            follow_redirects=False,
        )

    assert iframe.status_code == 200
    assert iframe.text == "user-web"
    assert asset.text == "user-web"
    assert captured == [
        "http://user-web:5173/?user_id=u1&bot_id=b1",
        "http://user-web:5173/assets/chat.js",
    ]
    assert standalone.status_code == 302
    assert standalone.headers["location"] == "/auth"


@pytest.mark.asyncio
async def test_manager_web_relays_authenticated_websocket_bidirectionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_client = httpx.AsyncClient
    client_received_response = asyncio.Event()
    upstream_received_request = asyncio.Event()
    captured: dict[str, Any] = {}

    async def identity(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"user_id": "u1"})

    def client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(identity))

    class FakeClientWebSocket:
        headers = {"origin": "https://manager.example.com"}
        cookies = {"openjiuwen_access_token": "valid-token"}
        url = SimpleNamespace(query="user_id=u1&bot_id=b1")

        def __init__(self) -> None:
            self.accepted = False
            self.sent: list[str] = []
            self.receive_count = 0

        async def accept(self) -> None:
            self.accepted = True

        async def receive(self) -> dict[str, Any]:
            self.receive_count += 1
            if self.receive_count == 1:
                return {"type": "websocket.receive", "text": "browser-request"}
            await client_received_response.wait()
            return {"type": "websocket.disconnect"}

        async def send_text(self, message: str) -> None:
            self.sent.append(message)
            client_received_response.set()

        async def send_bytes(self, message: bytes) -> None:
            self.sent.append(message.decode())
            client_received_response.set()

        async def close(self, code: int) -> None:
            captured["close_code"] = code

    class FakeUpstreamWebSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.iteration = 0

        async def send(self, message: str | bytes) -> None:
            self.sent.append(str(message))
            upstream_received_request.set()

        def __aiter__(self) -> FakeUpstreamWebSocket:
            return self

        async def __anext__(self) -> str:
            self.iteration += 1
            if self.iteration == 1:
                await upstream_received_request.wait()
                return "gateway-response"
            await asyncio.Event().wait()
            raise StopAsyncIteration

    class FakeConnect:
        def __init__(self, url: str, **kwargs: Any) -> None:
            captured["upstream_url"] = url
            captured["connect_kwargs"] = kwargs
            self.websocket = FakeUpstreamWebSocket()
            captured["upstream"] = self.websocket

        async def __aenter__(self) -> FakeUpstreamWebSocket:
            return self.websocket

        async def __aexit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr("manager_server.manager_web.httpx.AsyncClient", client_factory)
    monkeypatch.setattr("manager_server.manager_web.websockets.connect", FakeConnect)

    browser = FakeClientWebSocket()
    await _relay_websocket(
        browser,  # type: ignore[arg-type]
        "ws://gateway:19000/ws",
        "http://identity:8770",
    )

    assert captured["auth"] == "Bearer valid-token"
    assert captured["upstream_url"] == "ws://gateway:19000/ws?user_id=u1&bot_id=b1"
    assert captured["connect_kwargs"]["origin"] == "https://manager.example.com"
    assert browser.accepted
    assert captured["upstream"].sent == ["browser-request"]
    assert browser.sent == ["gateway-response"]
