from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from manager_server.manager_web import create_manager_web_app


class _ChunkStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"type":"res","id":"r2","ok":true}\n\n'
        yield b'data: {"type":"event","event":"chat.final"}\n\n'


@pytest.mark.asyncio
async def test_manager_web_relays_chat_iframe_sse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text("manager", encoding="utf-8")
    app = create_manager_web_app(
        tmp_path,
        "http://manager-api:8765",
        "http://identity:8770",
        gateway_sse="http://gateway:19001/web/invoke",
    )
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
        transport=httpx.ASGITransport(app=app), base_url="http://manager-web"
    )
    monkeypatch.setattr(
        "manager_server.manager_web.httpx.AsyncClient", client_factory
    )

    async with proxy_client:
        response = await proxy_client.post(
            "/web/invoke",
            headers={"Authorization": "Bearer valid-token"},
            json={"type": "req", "id": "r2", "method": "history.get"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.count("data:") == 2
    assert captured["url"] == "http://gateway:19001/web/invoke"
    assert captured["auth"] == "Bearer valid-token"
    assert b'"method":"history.get"' in captured["body"]


@pytest.mark.asyncio
async def test_manager_web_rejects_anonymous_chat_request(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("manager", encoding="utf-8")
    app = create_manager_web_app(
        tmp_path,
        "http://manager-api:8765",
        "http://identity:8770",
        gateway_sse="http://gateway:19001/web/invoke",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager-web"
    ) as client:
        response = await client.post(
            "/web/invoke",
            json={"type": "req", "id": "anonymous", "method": "chat.send"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_manager_web_serves_user_web_as_same_origin_iframe(
    tmp_path: Path,
) -> None:
    manager_dist = tmp_path / "manager"
    chat_dist = tmp_path / "chat"
    assets = chat_dist / "assets"
    manager_dist.mkdir()
    assets.mkdir(parents=True)
    (manager_dist / "index.html").write_text("manager", encoding="utf-8")
    (chat_dist / "index.html").write_text(
        '<script src="/chat/assets/chat.js"></script>', encoding="utf-8"
    )
    (assets / "chat.js").write_text("window.chatLoaded=true", encoding="utf-8")
    app = create_manager_web_app(
        manager_dist,
        "http://manager-api:8765",
        "http://identity:8770",
        chat_dist_root=chat_dist,
    )

    async with httpx.AsyncClient(
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
    assert "/chat/assets/chat.js" in iframe.text
    assert asset.text == "window.chatLoaded=true"
    assert standalone.status_code == 302
    assert standalone.headers["location"] == "/auth"
