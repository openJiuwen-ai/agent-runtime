"""A2A Manager-to-Gateway transport safety gate."""

from __future__ import annotations

import pytest

from manager_server.manager_config_push.client import gateway_request


@pytest.mark.asyncio
async def test_credential_replace_requires_https(monkeypatch: pytest.MonkeyPatch):
    async def endpoint(_jid: str) -> str:
        return "http://gateway.internal:8188"

    monkeypatch.setattr(
        "manager_server.manager_config_push.client.require_gateway_endpoint", endpoint
    )
    with pytest.raises(ValueError, match="only be synchronized over HTTPS"):
        await gateway_request(
            "gateway-1",
            "POST",
            "/api/v1/a2a-outbound-templates",
            {"credential": {"operation": "replace", "value": "secret"}},
        )


@pytest.mark.asyncio
async def test_credential_clear_does_not_require_https(monkeypatch: pytest.MonkeyPatch):
    sent: dict = {}

    async def endpoint(_jid: str) -> str:
        return "http://gateway.internal:8188"

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"data": {"result": "ok"}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, json):
            sent.update(method=method, url=url, json=json)
            return Response()

    monkeypatch.setattr(
        "manager_server.manager_config_push.client.require_gateway_endpoint", endpoint
    )
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )
    await gateway_request(
        "gateway-1",
        "POST",
        "/api/v1/a2a-outbound-templates",
        {"credential": {"operation": "clear"}},
    )
    assert sent["json"]["credential"] == {"operation": "clear"}


@pytest.mark.asyncio
async def test_credential_keep_does_not_require_https(monkeypatch: pytest.MonkeyPatch):
    sent: dict = {}

    async def endpoint(_jid: str) -> str:
        return "http://gateway.internal:8188"

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"data": {"result": "ok"}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, json):
            sent.update(method=method, url=url, json=json)
            return Response()

    monkeypatch.setattr(
        "manager_server.manager_config_push.client.require_gateway_endpoint", endpoint
    )
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )
    await gateway_request(
        "gateway-1",
        "PATCH",
        "/api/v1/a2a-outbound-templates/agent-1",
        {"template_name": "renamed", "credential": {"operation": "keep"}},
    )
    assert sent["json"]["credential"] == {"operation": "keep"}
