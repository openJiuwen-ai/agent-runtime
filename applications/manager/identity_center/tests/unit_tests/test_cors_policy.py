"""身份中心 CORS 来源白名单：通配源与携带凭证互斥。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from identity_center.app import cors_middleware_kwargs, create_app
from identity_center.infrastructure.config import Settings, settings

EVIL_ORIGIN = "https://evil.example"
PREFLIGHT_HEADERS = {
    "Origin": EVIL_ORIGIN,
    "Access-Control-Request-Method": "GET",
}
# 身份中心没有独立的健康检查路由；/openapi.json 不依赖数据库，简单请求可稳定拿到 200。
PROBE_PATH = "/openapi.json"


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_default_setting_is_same_origin_only():
    """默认值读字段定义，不实例化（实例会吃进程环境与 applications/manager/.env）。"""
    assert Settings.model_fields["cors_allow_origins"].default == ""


def test_env_alias_wires_origins_setting():
    """钉住环境变量名与字段的接线，防止 validation_alias 拼错后开关失效。"""
    parsed = Settings(
        _env_file=None,
        IDENTITY_CORS_ALLOW_ORIGINS="https://a.example, https://b.example",
    )
    assert parsed.cors_allow_origins == "https://a.example, https://b.example"


def test_empty_origins_disable_credentials():
    kwargs = cors_middleware_kwargs("")
    assert kwargs["allow_origins"] == []
    assert kwargs["allow_credentials"] is False


def test_whitelist_entries_are_trimmed_and_allow_credentials():
    kwargs = cors_middleware_kwargs(" https://a.example , https://b.example ")
    assert kwargs["allow_origins"] == ["https://a.example", "https://b.example"]
    assert kwargs["allow_credentials"] is True


def test_wildcard_forces_credentials_off():
    kwargs = cors_middleware_kwargs("*")
    assert kwargs["allow_origins"] == ["*"]
    assert kwargs["allow_credentials"] is False


@pytest.mark.asyncio
async def test_external_origin_preflight_gets_no_cors_headers(monkeypatch):
    monkeypatch.setattr(settings, "cors_allow_origins", "")
    app = create_app()
    async with _client(app) as client:
        response = await client.options(PROBE_PATH, headers=PREFLIGHT_HEADERS)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.asyncio
async def test_whitelisted_origin_preflight_echoes_origin_with_credentials(monkeypatch):
    monkeypatch.setattr(settings, "cors_allow_origins", "https://ops.example")
    app = create_app()
    headers = {
        "Origin": "https://ops.example",
        "Access-Control-Request-Method": "GET",
    }
    async with _client(app) as client:
        response = await client.options(PROBE_PATH, headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://ops.example"
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_whitelist_miss_has_no_allow_origin_header(monkeypatch):
    monkeypatch.setattr(settings, "cors_allow_origins", "https://ops.example")
    app = create_app()
    async with _client(app) as client:
        preflight = await client.options(PROBE_PATH, headers=PREFLIGHT_HEADERS)
        simple = await client.get(PROBE_PATH, headers={"Origin": EVIL_ORIGIN})
    assert preflight.status_code == 400
    assert simple.status_code == 200
    # Starlette 对非白名单来源仍会附带 Allow-Credentials（固定头），但没有
    # Allow-Origin，浏览器不会放行响应体；这里只钉住决定性的 Allow-Origin。
    assert "access-control-allow-origin" not in preflight.headers
    assert "access-control-allow-origin" not in simple.headers


@pytest.mark.asyncio
async def test_wildcard_config_keeps_credentials_off(monkeypatch):
    monkeypatch.setattr(settings, "cors_allow_origins", "*")
    app = create_app()
    async with _client(app) as client:
        response = await client.options(PROBE_PATH, headers=PREFLIGHT_HEADERS)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
