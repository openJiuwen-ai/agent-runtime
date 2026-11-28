# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""模型模板连通性探测单元测试。"""

from __future__ import annotations

import httpx
import pytest

from manager_server.core.template import model_template_connectivity as conn
from manager_server.core.template.model_template_connectivity import (
    ProbeTargetBlocked,
    build_probe_headers,
    classify_connection_error,
    models_list_url,
    normalize_model_api_base,
    probe_model_template_connection,
)

pytestmark = pytest.mark.unit

_PUBLIC_IP = "93.184.216.34"
_PRIVATE_IP = "10.1.2.3"


def _patch_resolve(monkeypatch, ip: str = _PUBLIC_IP):
    async def _resolve(hostname: str, port: int):
        return [ip]

    monkeypatch.setattr(conn, "_resolve_addresses", _resolve)


def _patch_client(monkeypatch, handler):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        return original(transport=httpx.MockTransport(handler), *args, **kwargs)

    monkeypatch.setattr(conn.httpx, "AsyncClient", factory)


def test_normalize_strips_chat_completions_suffix():
    assert (
        normalize_model_api_base("https://api.example.com/v1/chat/completions")
        == "https://api.example.com/v1"
    )
    assert models_list_url("https://api.example.com/v1/") == "https://api.example.com/v1/models"


def test_build_probe_headers_openai_and_anthropic():
    openai = build_probe_headers(
        api_key="sk-test",
        model_provider="OpenAI",
        data={
            "headers": {
                "X-Custom": "1",
                "Authorization": "Bearer hijack",
                "Host": "evil.example",
            }
        },
    )
    assert openai["Authorization"] == "Bearer sk-test"
    assert openai["X-Custom"] == "1"
    assert "Host" not in openai
    assert "host" not in {k.lower() for k in openai}

    anthropic = build_probe_headers(
        api_key="sk-ant",
        model_provider="Anthropic",
        data=None,
    )
    assert anthropic["x-api-key"] == "sk-ant"
    assert anthropic["anthropic-version"]


@pytest.mark.asyncio
async def test_probe_success_finds_model_id(monkeypatch):
    _patch_resolve(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers.get("Authorization") == "Bearer sk-ok"
        # IP pin：请求打到解析后的地址，Host 仍为原主机。
        assert request.url.host == _PUBLIC_IP
        assert request.headers.get("Host") == "api.example.com"
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "gpt-test"}, {"id": "other"}]},
        )

    _patch_client(monkeypatch, handler)
    result = await probe_model_template_connection(
        api_base="https://api.example.com/v1",
        api_key="sk-ok",
        model_id="gpt-test",
        model_provider="OpenAI",
        verify_ssl=False,
    )
    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["model_id_found"] is True


@pytest.mark.asyncio
async def test_probe_auth_failure(monkeypatch):
    _patch_resolve(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "invalid api key"}}
        )

    _patch_client(monkeypatch, handler)
    result = await probe_model_template_connection(
        api_base="https://api.example.com/v1",
        api_key="bad",
        model_id="gpt-test",
        model_provider="OpenAI",
    )
    assert result["ok"] is False
    assert result["status_code"] == 401
    assert result["error_code"] == "auth"
    assert "invalid api key" in (result["detail"] or "")


def test_classify_connection_error_by_status_and_markers():
    assert classify_connection_error(status_code=402, detail="Payment Required") == "balance"
    assert classify_connection_error(status_code=429, detail="") == "rate_limit"
    assert classify_connection_error(status_code=404, detail="") == "not_found"
    assert classify_connection_error(status_code=503, detail="") == "server"
    assert classify_connection_error(status_code=400, detail="bad request") == "http_error"
    assert (
        classify_connection_error(status_code=400, detail="Invalid API key provided")
        == "auth"
    )
    assert classify_connection_error(exc=httpx.ConnectTimeout("slow")) == "timeout"
    assert classify_connection_error(exc=httpx.ConnectError("refused")) == "network"
    assert classify_connection_error(exc=httpx.InvalidURL("bad port")) == "network"
    assert (
        classify_connection_error(exc=ProbeTargetBlocked("blocked")) == "target_blocked"
    )
    assert classify_connection_error(status_code=301, detail="") == "redirect"
    assert classify_connection_error(status_code=302, detail="") == "redirect"


@pytest.mark.asyncio
async def test_probe_invalid_port_returns_structured_failure():
    """非法端口在 urlsplit 阶段失败，应收成结构化 target_blocked，而不是 500。"""
    result = await probe_model_template_connection(
        api_base="http://example.com:abc/v1",
        api_key="sk-ok",
        model_id="gpt-test",
        model_provider="OpenAI",
    )
    assert result["ok"] is False
    assert result["error_code"] == "target_blocked"


@pytest.mark.asyncio
async def test_probe_treats_redirect_as_failure(monkeypatch):
    _patch_resolve(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://api.example.com/v1/models"},
            content=b"",
        )

    _patch_client(monkeypatch, handler)
    result = await probe_model_template_connection(
        api_base="http://api.example.com/v1",
        api_key="sk-ok",
        model_id="gpt-test",
        model_provider="OpenAI",
    )
    assert result["ok"] is False
    assert result["status_code"] == 302
    assert result["error_code"] == "redirect"


@pytest.mark.asyncio
async def test_probe_blocks_link_local_metadata(monkeypatch):
    _patch_resolve(monkeypatch, "169.254.169.254")
    result = await probe_model_template_connection(
        api_base="http://169.254.169.254/latest/meta-data",
        api_key="sk-ok",
        model_id="gpt-test",
        model_provider="OpenAI",
    )
    assert result["ok"] is False
    assert result["error_code"] == "target_blocked"


@pytest.mark.asyncio
async def test_probe_blocks_loopback(monkeypatch):
    _patch_resolve(monkeypatch, "127.0.0.1")
    result = await probe_model_template_connection(
        api_base="http://127.0.0.1:8080/v1",
        api_key="sk-ok",
        model_id="gpt-test",
        model_provider="OpenAI",
    )
    assert result["ok"] is False
    assert result["error_code"] == "target_blocked"


@pytest.mark.asyncio
async def test_probe_allows_private_rfc1918(monkeypatch):
    _patch_resolve(monkeypatch, _PRIVATE_IP)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == _PRIVATE_IP
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    _patch_client(monkeypatch, handler)
    result = await probe_model_template_connection(
        api_base="http://llm.internal/v1",
        api_key="sk-ok",
        model_id="local-model",
        model_provider="OpenAI",
        verify_ssl=False,
    )
    assert result["ok"] is True
    assert result["model_id_found"] is True


@pytest.mark.asyncio
async def test_probe_rejects_oversized_response(monkeypatch):
    _patch_resolve(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (conn.MAX_PROBE_RESPONSE_BYTES + 1),
            headers={"content-type": "application/json"},
        )

    _patch_client(monkeypatch, handler)
    result = await probe_model_template_connection(
        api_base="https://api.example.com/v1",
        api_key="sk-ok",
        model_id="gpt-test",
        model_provider="OpenAI",
    )
    assert result["ok"] is False
    assert result["error_code"] == "target_blocked"
    assert "size limit" in (result["detail"] or "")


@pytest.mark.asyncio
async def test_router_test_connection_for_saved_template(manager_api, monkeypatch):
    _patch_resolve(monkeypatch)
    h = manager_api
    created = await h.post_json(
        "/model-templates",
        {
            "template_name": "conn-probe",
            "model_type": ["default"],
            "api_base": "https://api.example.com/v1",
            "api_key": "sk-live",
            "model_id": "gpt-probe",
            "model_provider": "OpenAI",
            "timeout": 10,
            "verify_ssl": False,
        },
    )
    tid = created["template_id"]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Bearer sk-live" in request.headers.get("Authorization", "")
        return httpx.Response(200, json={"data": [{"id": "gpt-probe"}]})

    _patch_client(monkeypatch, handler)

    result = await h.post_json(f"/model-templates/{tid}/test-connection", {})
    assert result["ok"] is True
    assert result["model_id_found"] is True

    draft = await h.post_json(
        "/model-templates/test-connection",
        {
            "api_base": "https://api.example.com/v1",
            "api_key": "sk-live",
            "model_id": "missing-model",
            "model_provider": "OpenAI",
        },
    )
    assert draft["ok"] is False
    assert draft["model_id_found"] is False
    assert draft["error_code"] == "model_not_found"


@pytest.mark.asyncio
async def test_probe_fails_when_model_id_missing(monkeypatch):
    _patch_resolve(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-real"}]})

    _patch_client(monkeypatch, handler)
    result = await probe_model_template_connection(
        api_base="https://api.example.com/v1",
        api_key="sk-ok",
        model_id="乱",
        model_provider="OpenAI",
    )
    assert result["ok"] is False
    assert result["model_id_found"] is False
    assert result["error_code"] == "model_not_found"
