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


@pytest.mark.asyncio
@pytest.mark.parametrize("url,policy,allowed,port", [
    ("HTTPS://10.0.0.8", {}, False, 443),
    ("HTTPS://10.0.0.8", {"allow_private_network": True}, True, 443),
    ("HTTP://10.0.0.8", {"allow_private_network": True}, False, None),
    ("HTTP://10.0.0.8", {"allow_private_network": True, "allow_http": True}, True, 80),
    ("https://[fd00::1]", {"allow_private_network": True}, False, 443),
    ("https://[::1]", {"allow_private_network": True}, False, 443),
])
async def test_network_policy_explicit_boundaries(monkeypatch, url, policy, allowed, port):
    import asyncio
    import socket
    from manager_server.core.template.a2a_discovery import _validate_target

    ports = []

    async def resolve(host, resolved_port, **kwargs):
        ports.append(resolved_port)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, resolved_port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    if allowed:
        await _validate_target(url, **policy)
    else:
        with pytest.raises(ValueError):
            await _validate_target(url, **policy)
    assert ports == ([] if port is None else [port])


@pytest.mark.asyncio
@pytest.mark.parametrize("certificate_host", ["gateway.test", "wrong.test"])
async def test_pinned_transport_verifies_original_tls_hostname(monkeypatch, tmp_path, certificate_host):
    import asyncio
    import ssl
    from datetime import datetime, timedelta, timezone
    import httpx
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certificate_host)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(certificate_host)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    server_names, requests = [], []
    server_context.set_servername_callback(lambda sslobj, host, context: server_names.append(host))

    async def respond(reader, writer):
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]

    async def endpoint(_jid):
        return f"https://gateway.test:{port}"

    async def validated_target(url, **flags):
        # The policy boundary is tested separately; loopback is only the TLS fixture.
        return "gateway.test", "127.0.0.1"

    client_class = httpx.AsyncClient
    trust = ssl.create_default_context(cafile=str(cert_path))
    monkeypatch.setattr("manager_server.manager_config_push.client.require_gateway_endpoint", endpoint)
    monkeypatch.setattr("manager_server.core.template.a2a_discovery._validate_target", validated_target)
    monkeypatch.setattr(
        "manager_server.manager_config_push.client.httpx.AsyncClient",
        lambda **kwargs: client_class(verify=trust, **kwargs),
    )
    payload = {"credential": {"operation": "replace", "value": "test-token"}}
    async with server:
        if certificate_host == "gateway.test":
            await gateway_request("gateway-1", "POST", "/api/v1/a2a-outbound-templates", payload)
            assert f"Host: gateway.test:{port}".encode() in requests[0]
        else:
            with pytest.raises(ValueError, match="CERTIFICATE_VERIFY_FAILED"):
                await gateway_request("gateway-1", "POST", "/api/v1/a2a-outbound-templates", payload)
            assert requests == []
    assert server_names == ["gateway.test"]
