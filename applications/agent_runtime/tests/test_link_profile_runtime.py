"""Real Runtime ASGI security boundary; business handlers do not need K8s here."""

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from urllib.parse import urljoin

import httpx
import pytest
import uvicorn
from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.link_mtls import LinkMTLSConfig
from agent_runtime.main import create_app
from openjiuwen_runtime.foundation.security.link_certificate_bundle import (
    issue_bundle,
    materialize_role,
)
from openjiuwen_runtime.foundation.security.link_profile import LinkProfile
from openjiuwen_runtime.service.config import ServiceConfig


@pytest.fixture(name="bundle")
def certificate_bundle(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("JIUWENSWARM_LINK_"):
            monkeypatch.delenv(name)
    material = issue_bundle(
        mtls_deployment_id="test-deployment", endpoints={"runtime": "127.0.0.1:8091"}
    )
    for role in ("gateway", "runtime", "manager"):
        materialize_role(material, role, tmp_path / role)
    return tmp_path


def profile(bundle, role):
    return LinkProfile.load(str(bundle / role / "profile.json"))


@asynccontextmanager
async def serve(app, identity):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            lifespan="off",
            log_level="critical",
            **identity.server_kwargs(),
        )
    )
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


@pytest.mark.parametrize("mode", ["off", "observe"])
def test_incomplete_existing_identity_does_not_break_non_enforce(monkeypatch, mode):
    monkeypatch.setenv("JIUWENSWARM_LINK_MTLS_MODE", mode)
    monkeypatch.setenv("JIUWENSWARM_LINK_MTLS_PROFILE", "/not-installed/profile.json")
    c = LinkMTLSConfig.from_env()
    assert c.client_kwargs(role="agentserver") == {} and c.uvicorn_ssl_kwargs() == {}
    assert c.binding_headers() == {}


def test_runtime_real_middleware_enforces_peer_roles(bundle, tmp_path, monkeypatch):
    from openjiuwen_runtime.foundation.security import link_profile as profiles

    monkeypatch.setattr(profiles, "DEFAULT_IDENTITY_ROOT", bundle)
    monkeypatch.setenv("JIUWENSWARM_LINK_MTLS_MODE", "enforce")

    async def run():
        runtime, manager, gateway = (
            profile(bundle, r) for r in ("runtime", "manager", "gateway")
        )
        app = create_app(
            ServiceConfig.from_env(),
            AgentRuntimeConfig(mode="local"),
        )

        @app.asgi.get("/review-probe")
        async def probe():
            return {"ok": True}

        # Lifespan is deliberately off: this checks the actual production
        # middleware over TLS, not database lifecycle / autoscale / model output.
        async with serve(app.asgi, runtime) as url:
            async with httpx.AsyncClient(**manager.client_kwargs(role="runtime")) as c:
                assert (
                    await c.get(
                        urljoin(url, "/review-probe"), headers=manager.headers()
                    )
                ).status_code == 200
            async with httpx.AsyncClient(**gateway.client_kwargs(role="runtime")) as c:
                assert (
                    await c.get(
                        urljoin(url, "/review-probe"), headers=gateway.headers()
                    )
                ).status_code == 403

    asyncio.run(run())
