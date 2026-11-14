"""#179：用户面选路 Cookie（``jiuwenclaw_id``）的 Secure 属性。

HTTP NodePort 是默认部署形态，不能写死 Secure；HTTPS 下才加。开关可强制，
登出与写入必须取同一套属性，否则浏览器不删。
"""

from __future__ import annotations

from http.cookies import SimpleCookie
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from manager_server.core.user_console import UserConsoleService
from manager_server.infrastructure.config import Settings, settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.auth_guards import get_current_user
from manager_server.routers.user_console_routers import (
    public_user_console_router,
    user_console_router,
)

URL = "/api/v1/user-console/active-cluster"
COOKIE_ATTRIBUTES = ("path", "secure", "httponly", "samesite")


def make_client(scheme: str) -> httpx.AsyncClient:
    """按 scheme 构造客户端：ASGITransport 用 base_url 的 scheme 写 scope["scheme"]。"""

    def current_user() -> SimpleNamespace:
        return SimpleNamespace(user_id="u1", groups=[])

    def db_handler() -> None:
        return None

    app = FastAPI()
    app.include_router(user_console_router, prefix="/api/v1/user-console")
    app.include_router(public_user_console_router, prefix="/api/v1/user-console")
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db_handler] = db_handler
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"{scheme}://manager.test"
    )


@pytest.fixture(autouse=True)
def admit_and_reset_switch(monkeypatch):
    async def admitted(*args, **kwargs):
        return True

    monkeypatch.setattr(UserConsoleService, "user_can_access_instance", admitted)
    monkeypatch.setattr(settings, "active_cluster_cookie_secure", "auto")


def set_cookie_of(response: httpx.Response) -> SimpleCookie:
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    return parsed


def is_secure(response: httpx.Response) -> bool:
    """SimpleCookie 对缺席的 Secure 返回空串，在场返回 True。"""
    return set_cookie_of(response)["jiuwenclaw_id"]["secure"] is True


def test_env_switch_reaches_the_setting():
    """部署模板注入的是 MANAGER_ACTIVE_CLUSTER_COOKIE_SECURE，别名错则开关静默失效。"""
    assert Settings(_env_file=None).active_cluster_cookie_secure == "auto"
    injected = Settings(
        _env_file=None, MANAGER_ACTIVE_CLUSTER_COOKIE_SECURE="true"
    ).active_cluster_cookie_secure
    assert injected == "true"


@pytest.mark.asyncio
async def test_https_request_marks_cookie_secure():
    async with make_client("https") as client:
        response = await client.post(URL, json={"jiuwenclaw_id": "jid1"})

    assert response.status_code == 200
    morsel = set_cookie_of(response)["jiuwenclaw_id"]
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "lax"
    assert morsel["path"] == "/"


@pytest.mark.asyncio
async def test_http_request_keeps_cookie_usable():
    """默认 HTTP NodePort 部署：不得带 Secure，否则登录态直接失效。"""
    async with make_client("http") as client:
        response = await client.post(URL, json={"jiuwenclaw_id": "jid1"})

    assert response.status_code == 200
    assert is_secure(response) is False


@pytest.mark.asyncio
async def test_switch_true_forces_secure_over_http(monkeypatch):
    monkeypatch.setattr(settings, "active_cluster_cookie_secure", "true")
    async with make_client("http") as client:
        response = await client.post(URL, json={"jiuwenclaw_id": "jid1"})

    assert is_secure(response) is True


@pytest.mark.asyncio
async def test_switch_false_drops_secure_over_https(monkeypatch):
    monkeypatch.setattr(settings, "active_cluster_cookie_secure", "FALSE")
    async with make_client("https") as client:
        response = await client.post(URL, json={"jiuwenclaw_id": "jid1"})

    assert is_secure(response) is False


@pytest.mark.asyncio
async def test_unknown_switch_value_falls_back_to_scheme(monkeypatch):
    monkeypatch.setattr(settings, "active_cluster_cookie_secure", "ture")
    async with make_client("https") as client:
        response = await client.post(URL, json={"jiuwenclaw_id": "jid1"})

    assert is_secure(response) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["https", "http"])
async def test_logout_cookie_attributes_match_set_cookie(scheme):
    async with make_client(scheme) as client:
        written_response = await client.post(URL, json={"jiuwenclaw_id": "jid1"})
        cleared_response = await client.delete(URL)

    written = set_cookie_of(written_response)
    cleared = set_cookie_of(cleared_response)

    written_morsel = written["jiuwenclaw_id"]
    cleared_morsel = cleared["jiuwenclaw_id"]
    assert written_morsel.key == cleared_morsel.key
    for attribute in COOKIE_ATTRIBUTES:
        assert written_morsel[attribute] == cleared_morsel[attribute], attribute
    assert "Max-Age=0" in cleared_response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_forwarded_proto_is_not_parsed_by_the_app():
    """跨 Pod 反代下 uvicorn 不信任该头；应用层也不自行解析，避免被伪造降级。"""
    async with make_client("http") as client:
        response = await client.post(
            URL,
            json={"jiuwenclaw_id": "jid1"},
            headers={"X-Forwarded-Proto": "https"},
        )

    assert is_secure(response) is False
