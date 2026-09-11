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
    with pytest.raises(ValueError, match="network access settings"):
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


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["10.0.0.8", "8.8.8.8", "127.0.0.1", "169.254.169.254", "100.64.0.1"])
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("flags", [
    (False, False, False), (True, True, False), (True, False, True),
    (False, True, True), (True, True, True),
])
@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_credential_sync_network_policy(monkeypatch, host, scheme, flags, method):
    import httpx

    sent = []
    policy = dict(zip(("allow_http", "allow_private_network", "allow_public_http"), flags))
    allowed = (
        (host == "8.8.8.8" or (host == "10.0.0.8" and flags[1]))
        and (scheme == "https" or (flags[0] and (host != "8.8.8.8" or flags[2])))
    )

    async def endpoint(_jid):
        return f"{scheme}://{host}:8188"

    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={"data": {"result": "ok"}})

    client_class = httpx.AsyncClient
    monkeypatch.setattr("manager_server.manager_config_push.client.require_gateway_endpoint", endpoint)
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    payload = {"credential": {"operation": "replace", "value": "secret"},
               "data": {"network_policy": policy}}
    if allowed:
        await gateway_request("gateway-1", method, "/api/v1/a2a-outbound-templates", payload)
        assert len(sent) == 1
        assert sent[0].method == method
        assert sent[0].headers["Host"] == f"{host}:8188"
        assert sent[0].extensions["sni_hostname"] == host
    else:
        with pytest.raises(ValueError, match="network access settings"):
            await gateway_request("gateway-1", method, "/api/v1/a2a-outbound-templates", payload)
        assert sent == []


@pytest.mark.asyncio
async def test_credential_sync_pins_validated_dns_address(monkeypatch):
    import asyncio
    import socket
    import httpx

    sent = []

    async def endpoint(_jid):
        return "https://gateway.internal:8188"

    async def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 8188))]

    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={"data": {"result": "ok"}})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr("manager_server.manager_config_push.client.require_gateway_endpoint", endpoint)
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    await gateway_request("gateway-1", "POST", "/api/v1/a2a-outbound-templates", {
        "credential": {"operation": "replace", "value": "secret"},
        "data": {"network_policy": {"allow_private_network": True}},
    })
    assert sent[0].url.host == "10.0.0.8"
    assert sent[0].headers["Host"] == "gateway.internal:8188"
    assert sent[0].extensions["sni_hostname"] == "gateway.internal"
