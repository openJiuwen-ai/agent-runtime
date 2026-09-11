"""Real local TLS sockets; no Kubernetes, Manager or mocked TLS validation."""

import asyncio
import json
import socket
import ssl
from contextlib import asynccontextmanager
from urllib.parse import urljoin

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from openjiuwen_runtime.foundation.security.link_profile import (
    LinkProfile,
    LinkProfileError,
)
from starlette.responses import JSONResponse, StreamingResponse

from ..link_mtls_helpers import (
    atomic_json,
    provision,
)


@pytest.fixture(name="bundle")
def certificate_bundle(tmp_path, monkeypatch):
    for name in (
        "JIUWENSWARM_LINK_MTLS_PROFILE",
        "JIUWENSWARM_LINK_MTLS_CA_FILE",
        "JIUWENSWARM_LINK_MTLS_CERT_FILE",
        "JIUWENSWARM_LINK_MTLS_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "bundle"
    provision(
        root,
        mtls_deployment_id="test-deployment",
        endpoints={
            "gateway": "127.0.0.1:8775",
            "runtime": "127.0.0.1:8091",
            "agentserver": "127.0.0.1:8766",
        },
    )
    return root


def profile(bundle, role):
    return LinkProfile.load(str(bundle / role / "profile.json"))


def update(p, **values):
    data = json.loads(p.path.read_text())
    data.update(values)
    atomic_json(p.path, data)


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "link://runtime/api/session/route?x=1",
            "https://127.0.0.1:8091/api/session/route?x=1",
        ),
        (
            "http://127.0.0.1:8091/api/session/route",
            "https://127.0.0.1:8091/api/session/route",
        ),
        ("127.0.0.1:8091", "https://127.0.0.1:8091"),
        ("https://customer.example:9000/x", "https://customer.example:9000/x"),
    ],
)
def test_managed_endpoint(bundle, url, expected):
    assert profile(bundle, "gateway").endpoint(url, role="runtime") == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://customer.example:9000/x",
        "http://127.0.0.1:8092",
        "http://user:password@127.0.0.1:8091",
        "https://127.0.0.1:8091/#fragment",
        "link://gateway",
        "ws://127.0.0.1:8091",
        "127.0.0.1:8092",
    ],
)
def test_external_plaintext_and_ambiguous_endpoint_rejected(bundle, url):
    with pytest.raises(LinkProfileError):
        profile(bundle, "gateway").endpoint(url, role="runtime")


@pytest.mark.parametrize("authority", ["runtime.default.svc:8091", "[::1]:8091"])
def test_dns_and_ipv6_bare_authorities(bundle, authority):
    p = profile(bundle, "gateway")
    update(p, endpoints={"runtime": authority})
    assert p.endpoint(authority, role="runtime") == "https://" + authority


def test_material_permissions_and_no_issuer_key(bundle):
    assert not list(bundle.rglob("ca.key"))
    for p in bundle.rglob("tls.key"):
        assert p.stat().st_mode & 0o777 == 0o600
    for p in bundle.rglob("profile.json"):
        assert p.stat().st_mode & 0o777 == 0o600
    with pytest.raises(LinkProfileError, match="already exists"):
        provision(bundle, mtls_deployment_id="other", endpoints={})


def test_role_must_match_component(bundle):
    p = profile(bundle, "gateway")
    with pytest.raises(LinkProfileError, match="role"):
        LinkProfile.load(str(p.path), roles={"runtime"})


def test_changed_epoch_and_revocation_rechecked(bundle):
    p = profile(bundle, "gateway")
    update(p, mtls_binding_epoch=2)
    with pytest.raises(LinkProfileError, match="restart"):
        p.headers()
    update(p, mtls_binding_epoch=1, status="revoked")
    with pytest.raises(LinkProfileError, match="not active"):
        p.headers()


def test_external_material_override_conflict_is_rejected(bundle, monkeypatch):
    monkeypatch.setenv("JIUWENSWARM_LINK_MTLS_CA_FILE", "/old/ca.crt")
    with pytest.raises(LinkProfileError, match="conflicts"):
        profile(bundle, "gateway")


def test_malformed_material_is_validation_error(bundle):
    p = profile(bundle, "gateway")
    update(p, tls=[])
    with pytest.raises(LinkProfileError, match="TLS file references"):
        LinkProfile.load(str(p.path))


@asynccontextmanager
async def serve(app, p):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        lifespan="off",
        log_level="critical",
        **p.server_kwargs(),
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        yield f"https://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


def guarded_app(p):
    app = FastAPI()
    app.state.accepted = 0

    @app.middleware("http")
    async def guard(request: Request, call_next):
        try:
            p.authorize(request.scope, request.headers)
        except LinkProfileError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return await call_next(request)

    @app.api_route("/probe", methods=["GET", "POST"])
    async def probe():
        app.state.accepted += 1
        return {"ok": True}

    @app.post("/api/session/route")
    async def route():
        return {"ok": True}

    @app.get("/stream")
    async def stream():
        async def chunks():
            yield "event: accepted\ndata: {}\n\n"
            await asyncio.sleep(0.01)
            yield "event: final\ndata: {}\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    return app


def test_actual_tls_roles_pins_headers_stream_and_revocation(bundle):
    async def run():
        runtime = profile(bundle, "runtime")
        manager = profile(bundle, "manager")
        gateway = profile(bundle, "gateway")
        app = guarded_app(runtime)
        async with serve(app, runtime) as url:
            async with httpx.AsyncClient(
                **manager.client_kwargs(role="runtime")
            ) as client:
                assert (
                    await client.get(urljoin(url, "/probe"), headers=manager.headers())
                ).status_code == 200
                # Same CA and correct headers, but wrong client role.
                async with httpx.AsyncClient(
                    verify=runtime.ssl_context()
                ) as wrong_role:
                    assert (
                        await wrong_role.get(
                            urljoin(url, "/probe"), headers=runtime.headers()
                        )
                    ).status_code == 403
                assert (
                    await client.get(urljoin(url, "/probe"), headers={})
                ).status_code == 403
                async with client.stream(
                    "GET", urljoin(url, "/stream"), headers=manager.headers()
                ) as response:
                    assert response.status_code == 200
                    body = (await response.aread()).decode()
                    assert "event: accepted" in body and "event: final" in body
                async with httpx.AsyncClient(
                    **gateway.client_kwargs(role="runtime")
                ) as gw:
                    assert (
                        await gw.post(
                            urljoin(url, "/api/session/route"),
                            headers=gateway.headers(),
                        )
                    ).status_code == 200
                # Same-CA certificate loses its authorization WITHOUT a TLS restart.
                peers = runtime.current()["peers"]
                peers.pop("manager")
                update(runtime, peers=peers)
                assert (
                    await client.get(urljoin(url, "/probe"), headers=manager.headers())
                ).status_code == 403
                peers["manager"] = manager.current()["peers"]["manager"]
                update(runtime, peers=peers)
                assert (
                    await client.get(urljoin(url, "/probe"), headers=manager.headers())
                ).status_code == 200
                update(runtime, status="revoked")
                assert (
                    await client.get(urljoin(url, "/probe"), headers=manager.headers())
                ).status_code == 403

    asyncio.run(run())


def test_server_pin_checked_before_sending_http_body(bundle):
    async def run():
        runtime, manager = profile(bundle, "runtime"), profile(bundle, "manager")
        app = guarded_app(runtime)
        async with serve(app, runtime) as url:
            async with httpx.AsyncClient(
                **manager.client_kwargs(role="gateway")
            ) as client:
                with pytest.raises(LinkProfileError, match="peer certificate"):
                    await client.post(
                        urljoin(url, "/probe"),
                        content=b"must-not-send",
                        headers=manager.headers(),
                    )
            assert app.state.accepted == 0
            async with httpx.AsyncClient(
                **manager.client_kwargs(role="runtime")
            ) as client:
                assert (
                    await client.post(urljoin(url, "/probe"), headers=manager.headers())
                ).status_code == 200
            assert app.state.accepted == 1

    asyncio.run(run())


def test_instance_binding_pin_overrides_profile_role_pin(bundle):
    async def run():
        runtime, manager = profile(bundle, "runtime"), profile(bundle, "manager")
        expected = runtime.current()["peers"]["runtime"][0]
        async with serve(guarded_app(runtime), runtime) as url:
            async with httpx.AsyncClient(
                **manager.client_kwargs(
                    role="runtime", expected_fingerprints={expected}
                )
            ) as client:
                assert (
                    await client.get(urljoin(url, "/probe"), headers=manager.headers())
                ).status_code == 200
            async with httpx.AsyncClient(
                **manager.client_kwargs(
                    role="runtime", expected_fingerprints={"0" * 64}
                )
            ) as client:
                with pytest.raises(LinkProfileError, match="instance binding"):
                    await client.get(urljoin(url, "/probe"), headers=manager.headers())

    asyncio.run(run())


def test_missing_certificate_and_wrong_hostname_rejected(bundle):
    async def run():
        runtime, manager = profile(bundle, "runtime"), profile(bundle, "manager")
        async with serve(guarded_app(runtime), runtime) as url:
            async with httpx.AsyncClient(
                verify=ssl.create_default_context(cafile=runtime.ca_file)
            ) as no_cert:
                with pytest.raises(httpx.TransportError):
                    await no_cert.get(urljoin(url, "/probe"), headers=manager.headers())
            async with httpx.AsyncClient(
                **manager.client_kwargs(role="runtime")
            ) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.get(
                        urljoin(url.replace("127.0.0.1", "localhost"), "/probe"),
                        headers=manager.headers(),
                    )
                assert (
                    await client.get(urljoin(url, "/probe"), headers=manager.headers())
                ).status_code == 200

    asyncio.run(run())
