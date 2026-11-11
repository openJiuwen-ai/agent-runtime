from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from manager_server.manager_web import create_manager_web_app


def _manager_app(tmp_path: Path):
    (tmp_path / "index.html").write_text("manager", encoding="utf-8")
    return create_manager_web_app(
        tmp_path,
        "http://manager-api:8765",
        "http://identity:8770",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/chat/"),
    ("GET", "/chat/assets/main.js"),
    ("POST", "/gateway-api/v1/chat/completions"),
    ("GET", "/file-api/raw-file"),
    ("GET", "/share-api/view"),
    ("GET", "/ws"),
    ("GET", "/api/v1/user-console/agent-contexts"),
    ("GET", "/manager-api/v1/user-console/agent-contexts"),
])
async def test_management_origin_does_not_serve_user_web(tmp_path: Path, method: str, path: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_manager_app(tmp_path)), base_url="http://manager-web",
        cookies={"openjiuwen_access_token": "manager-token"},
    ) as client:
        assert (await client.request(method, path)).status_code == 404


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
async def test_identity_proxy_pins_the_manager_login_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _manager_app(tmp_path)
    original_client = httpx.AsyncClient
    captured: dict[str, str | None] = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["surface"] = request.headers.get("x-jiuwen-web-surface")
        return httpx.Response(
            200,
            json={"code": 200, "data": {"contexts": []}},
        )

    def client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(upstream))

    monkeypatch.setattr("manager_server.manager_web.httpx.AsyncClient", client_factory)
    async with original_client(
        transport=httpx.ASGITransport(app=app), base_url="http://manager-web"
    ) as client:
        response = await client.get(
            "/idp/v1/auth/federation/connections",
            headers={"X-Jiuwen-Web-Surface": "user"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["contexts"] == []
    assert captured == {
        "url": "http://identity:8770/v1/auth/federation/connections",
        "surface": "manager",
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
