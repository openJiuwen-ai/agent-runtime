"""Real-process Identity → Manager → Gateway/Runtime and Web proxy acceptance.

Run with JIUWENSWARM_REPO=/path/to/jiuwenswarm; build both frontend dist directories
first. A local redis-server executable is required. Databases, keys, passwords and
ports are isolated per run. No FastAPI dependency, token, guard or HTTP client is
mocked. Runtime uses its official local/FakeK8s mode (not a real cluster).
"""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import jwt
import pytest


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def append_url_path(base: str, *parts: str) -> str:
    """Append URL path segments, retaining the final route's trailing slash."""
    url = httpx.URL(base)
    segments = [url.path.rstrip("/")]
    for part in parts:
        segment = part.strip("/")
        if segment:
            segments.append(segment)
    if parts and parts[-1].endswith("/"):
        segments.append("")
    return str(url.copy_with(path="/".join(segments)))


def local_browser_context(browser):
    """Use fallback fonts; only local application APIs belong to this E2E."""
    context = browser.new_context()

    def reject_optional_font(route):
        route.abort()

    # Optional external fonts must not hang screenshots in offline CI. No
    # Identity, Manager, Gateway, Runtime or Web API request is intercepted.
    context.route("https://fonts.googleapis.com/**", reject_optional_font)
    context.route("https://fonts.gstatic.com/**", reject_optional_font)
    return context


@pytest.fixture(name="services", scope="module")
def services_fixture(tmp_path_factory):
    runtime = Path(__file__).resolve().parents[5]
    swarm = Path(os.environ["JIUWENSWARM_REPO"]).resolve()
    redis_exe = shutil.which("redis-server")
    assert redis_exe, "redis-server is required for the real Gateway enterprise store"
    for dist in (
        runtime / "applications/manager/manager_web/dist",
        swarm / "jiuwenswarm/channels/web/frontend/dist",
    ):
        assert (dist / "index.html").is_file(), f"Build frontend first: {dist}"
    directory = tmp_path_factory.mktemp("manager-auth-http")
    service_names = (
        "identity",
        "manager",
        "runtime",
        "receiver",
        "manager_web",
        "user_web",
        "redis",
    )
    ports = {name: free_port() for name in service_names}
    urls = {name: f"http://127.0.0.1:{port}" for name, port in ports.items()}
    password = secrets.token_urlsafe(24)
    env = os.environ.copy()
    # Explicit isolated configuration: never load/use personal accounts or DBs.
    env.update(
        {
            "IDENTITY_SQLITE_PATH": str(directory / "identity.db"),
            "IDENTITY_DB_TYPE": "sqlite",
            "IDENTITY_ADMIN_PASSWORD": password,
            "IDENTITY_USER1_PASSWORD": password,
            "IDENTITY_ACCESS_TTL": "30",
            "IDENTITY_FEDERATION_DEMO_ENABLED": "false",
            "MANAGER_SQLITE_PATH": str(directory / "manager.db"),
            "MANAGER_DB_TYPE": "sqlite",
            "IDENTITY_PUBLIC_KEY_URL": append_url_path(urls["identity"], "/v1/auth/public_key"),
            "REDIS_URL": "",
            "MANAGER_HEARTBEAT_SCAN_INTERVAL_SECONDS": "3600",
            "AGENT_RUNTIME_SQLITE_PATH": str(directory / "runtime.db"),
            "JIUWENSWARM_DATA_DIR": str(directory / "swarm-data"),
            "JIUWENSWARM_EDITION": "enterprise",
            "LOGIN_AUTH_SIMULATE": "false",
            "WORKSPACE_QUOTA_ENABLED": "true",
            "JIUWENSWARM_LINK_MTLS_MODE": "off",
            "JIUWENSWARM_LINK_PROFILE": "",
            "GATEWAY_DB_TYPE": "sqlite",
            "GATEWAY_SQLITE_PATH": str(directory / "gateway.db"),
            "AUTH_E2E_REDIS_PORT": str(ports["redis"]),
            "GATEWAY_CONFIG_HTTP_PORT": str(ports["receiver"]),
            "GATEWAY_RUNTIME_MANAGER_URL": urls["runtime"],
            "USER_WEB_IDP_TARGET": urls["identity"],
            "USER_WEB_MANAGER_TARGET": urls["manager"],
            "USER_WEB_SKIP_DEPENDENCY_PROBE": "false",
            "PYTHONPATH": os.pathsep.join(
                (str(swarm), str(swarm / "tests/system_tests/enterprise"))
            ),
        }
    )
    processes = []

    def start(name, args, health):
        # Popen duplicates the descriptor for the child; the parent's copy can
        # close immediately, including when process creation raises.
        with (directory / f"{name}.log").open("w") as stream:
            proc = subprocess.Popen(
                args, cwd=directory, env=env, stdout=stream, stderr=subprocess.STDOUT
            )
        processes.append(proc)
        deadline = time.monotonic() + 45
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    pytest.fail(f"{name} exited; inspect {directory / (name + '.log')}")
                try:
                    if client.get(health).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        pytest.fail(f"{name} readiness timeout; inspect {directory / (name + '.log')}")

    def uvicorn(name, module, factory=False):
        args = [
            sys.executable,
            "-m",
            "uvicorn",
            module,
            "--host",
            "127.0.0.1",
            "--port",
            str(ports[name]),
            "--no-access-log",
        ]
        return args + (["--factory"] if factory else [])

    try:
        with (directory / "redis.log").open("w") as stream:
            processes.append(
                subprocess.Popen(
                    [
                        redis_exe,
                        "--bind",
                        "127.0.0.1",
                        "--port",
                        str(ports["redis"]),
                        "--save",
                        "",
                        "--appendonly",
                        "no",
                    ],
                    cwd=directory,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            )
        start(
            "identity",
            uvicorn("identity", "identity_center.app:app"),
            append_url_path(urls["identity"], "/v1/auth/public_key"),
        )
        start(
            "manager",
            uvicorn("manager", "manager_server.app:app"),
            append_url_path(urls["manager"], "/api/health"),
        )
        start(
            "runtime",
            [
                sys.executable,
                "-m",
                "agent_runtime.cli",
                "--mode",
                "local",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports["runtime"]),
            ],
            append_url_path(urls["runtime"], "/healthz"),
        )
        start(
            "receiver",
            uvicorn("receiver", "manager_auth_receiver_app:create_app", True),
            append_url_path(urls["receiver"], "/api/health"),
        )
        start(
            "user_web",
            [
                sys.executable,
                "-m",
                "jiuwenswarm.channels.web.app_web",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports["user_web"]),
                "--dist",
                str(swarm / "jiuwenswarm/channels/web/frontend/dist"),
            ],
            append_url_path(urls["user_web"], "/health"),
        )
        start(
            "manager_web",
            [
                sys.executable,
                "-m",
                "manager_server.manager_web",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports["manager_web"]),
                "--proxy-target",
                urls["manager"],
                "--idp-target",
                urls["identity"],
                "--user-web-target",
                urls["user_web"],
            ],
            append_url_path(urls["manager_web"], "/"),
        )
        with httpx.Client(trust_env=False, timeout=20) as client:
            yield client, urls, password, directory
    finally:
        for proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def login(client, origin, username, password):
    response = client.post(
        append_url_path(origin, "/idp/v1/auth/token"),
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, f"Identity login failed with status {response.status_code}"
    return response.json()


def test_login_role_crud_proxy_and_downstream_persistence(services):
    client, urls, password, directory = services
    manager, web, user_web = urls["manager"], urls["manager_web"], urls["user_web"]
    admin_session = login(client, web, "admin", password)
    user_session = login(client, user_web, "user1", password)
    admin = {"Authorization": "Bearer " + admin_session["access_token"]}
    user = {"Authorization": "Bearer " + user_session["access_token"]}

    def call(method, path, body=None, headers=None, status=200):
        request_headers = admin if headers is None else headers
        response = client.request(
            method, httpx.URL(manager).join(path), headers=request_headers, json=body
        )
        assert response.status_code == status, (
            method,
            path,
            response.status_code,
            response.text[:500],
        )
        return response.json().get("data") if status == 200 else None

    # Login identity is separate from product authorization.
    me = call("GET", "/api/v1/authz/me")
    assert me["is_platform_admin"] and "platform_admin" in me["role_ids"]
    regular = call("GET", append_url_path(user_web, "/manager-api/v1/authz/me"), headers=user)
    assert not regular["manager_access"] and not regular["is_platform_admin"]
    for origin, prefix in (
        (manager, "/api"),
        (web, "/api"),
        (web, "/manager-api"),
        (user_web, "/manager-api"),
    ):
        for headers, status in (({}, 401), ({"Authorization": "Bearer bogus"}, 401), (user, 403)):
            call(
                "GET",
                append_url_path(origin, prefix, "/v1/instances/"),
                headers=headers,
                status=status,
            )
        call(
            "DELETE",
            append_url_path(origin, prefix, "/v1/user-console/active-cluster"),
            headers={},
        )

    instance = call(
        "POST",
        "/api/v1/instances/",
        {
            "jiuwenclaw_name": "auth-e2e",
            "space_id": "auth-e2e",
            "gateway_host": urls["receiver"],
            "runtime_host": urls["runtime"],
            "user_web_host": user_web,
            "gateway_web_http_host": urls["receiver"],
            "gateway_web_ws_host": urls["receiver"].replace("http://", "ws://"),
        },
    )
    jid = instance["jiuwenclaw_id"]
    scoped = f"/api/v1/instances/{jid}"
    assert call("GET", scoped)["jiuwenclaw_name"] == "auth-e2e"
    call("PATCH", scoped, {"jiuwenclaw_name": "auth-e2e-updated"})
    call("PUT", append_url_path(scoped, "/logging"), {"level": "WARNING", "gateway": "ERROR"})
    assert call("GET", append_url_path(scoped, "/logging"))["level"] == "WARNING"
    # No downstream-auth override: Manager's real HTTP push changes Gateway DB.
    with sqlite3.connect(directory / "gateway.db") as db:
        assert db.execute("SELECT level FROM logging_config").fetchone()[0] == "WARNING"
    call("PUT", append_url_path(scoped, "/logging"), {"level": "DEBUG"}, headers=user, status=403)
    with sqlite3.connect(directory / "gateway.db") as db:
        assert db.execute("SELECT level FROM logging_config").fetchone()[0] == "WARNING"

    model = call(
        "POST",
        "/api/v1/model-templates",
        {
            "template_name": "auth-model",
            "model_id": "test-model",
            "model_provider": "openai",
            "api_base": "https://example.invalid/v1",
            "api_key": "test-placeholder",
            "model_type": ["default"],
        },
    )
    tid = model["template_id"]
    call("PATCH", f"/api/v1/model-templates/{tid}", {"template_name": "auth-model-updated"})
    assert call("GET", f"/api/v1/model-templates/{tid}")["template_name"] == "auth-model-updated"
    call("DELETE", f"/api/v1/model-templates/{tid}", headers=user, status=403)
    assert call("GET", f"/api/v1/model-templates/{tid}")["template_id"] == tid
    call("DELETE", f"/api/v1/model-templates/{tid}")

    # Role changes are authoritative for an ALREADY issued Identity token.
    call(
        "POST",
        "/api/v1/authz/roles",
        {
            "role_id": "reader",
            "name": "Quota reader",
            "scope": "admin",
            "permission_ids": ["quota:read"],
        },
    )
    call("POST", "/api/v1/authz/roles/reader/users", {"user_ids": ["user1"]})
    call("GET", "/api/v1/instances/", headers=user)
    call("GET", append_url_path(scoped, "/workspace-quota/policies"), headers=user)
    call(
        "POST",
        append_url_path(scoped, "/workspace-quota/policies"),
        {"policy_name": "denied", "limit_bytes": 1024},
        headers=user,
        status=403,
    )
    call("GET", "/api/v1/authz/roles", headers=user, status=403)
    call(
        "POST",
        "/api/v1/user-console/active-cluster",
        {"jiuwenclaw_id": jid},
        headers=user,
        status=403,
    )
    call("PATCH", "/api/v1/authz/roles/reader", {"enabled": False})
    call("GET", "/api/v1/instances/", headers=user, status=403)
    call("PUT", "/api/v1/authz/roles/reader/users", {"user_ids": []})
    call("DELETE", "/api/v1/authz/roles/reader")

    # Instance admission is independent of Manager roles, including on a cache
    # hit. Revocation must immediately reject the same valid JWT and cookie.
    agent = call(
        "POST", "/api/v1/agent-templates/", {"template_name": "auth-agent", "template_ref": {}}
    )
    agent_resource = call(
        "POST",
        append_url_path(scoped, "/agent-resources"),
        {
            "resource_name": "auth-agent-resource",
            "ref_template_id": agent["template_id"],
            "match_exprs": ["user_id == 'user1'"],
        },
    )
    call("POST", append_url_path(scoped, "/users"), {"ids": ["user1"]})
    contexts = call(
        "GET",
        append_url_path(user_web, "/manager-api/v1/user-console/agent-contexts"),
        headers=user,
    )["contexts"]
    assert len(contexts) == 1 and contexts[0]["jiuwenclaw_id"] == jid
    selected = client.post(
        append_url_path(user_web, "/manager-api/v1/user-console/active-cluster"),
        headers=user,
        json={"jiuwenclaw_id": jid},
    )
    assert selected.status_code == 200
    assert "HttpOnly" in selected.headers["set-cookie"]
    cookie_headers = {**user, "Cookie": f"jiuwenclaw_id={jid}"}
    resolved = client.get(
        append_url_path(manager, "/api/v1/user-console/user-face-upstream"), headers=cookie_headers
    )
    assert resolved.status_code == 200
    assert resolved.headers["x-user-web-upstream"] == user_web
    assert resolved.headers["cache-control"] == "no-store"
    call("GET", "/api/v1/instances/", headers=user, status=403)
    call("DELETE", append_url_path(scoped, "/users"), {"ids": ["user1"]})
    assert (
        client.get(
            append_url_path(manager, "/api/v1/user-console/user-face-upstream"),
            headers=cookie_headers,
        ).status_code
        == 403
    )
    call(
        "POST",
        "/api/v1/user-console/active-cluster",
        {"jiuwenclaw_id": jid},
        headers=user,
        status=403,
    )
    call(
        "DELETE",
        append_url_path(scoped, "/agent-resources", agent_resource["items"][0]["resource_id"]),
    )
    call("DELETE", append_url_path("/api/v1/agent-templates", agent["template_id"]))

    # Exercise Manager -> Runtime config_sync, not merely the Runtime health API.
    call(
        "POST",
        "/api/v1/container-templates",
        {
            "template_name": "auth-container",
            "container_id": "auth-agent",
            "name": "agent",
            "image": "example.invalid/agent:test",
            "ports": [{"name": "sse", "containerPort": 8080}],
        },
    )
    template = call(
        "POST",
        "/api/v1/service-config-templates",
        {
            "template_name": "auth-service",
            "main_container_id": "auth-agent",
            "min_idle_pods": 0,
        },
    )
    resource = call(
        "POST",
        append_url_path(scoped, "/service-resources"),
        {
            "resource_name": "auth-resource",
            "ref_template_id": template["template_id"],
            "match_exprs": ["user_id == 'user1'"],
        },
    )
    rid = resource["items"][0]["resource_id"]
    from agent_runtime.session_manager.config_store import ROUTING_SCOPE_TABLE

    with sqlite3.connect(directory / "runtime.db") as db:
        assert db.execute(f"SELECT count(*) FROM {ROUTING_SCOPE_TABLE}").fetchone()[0] == 1
    call("DELETE", append_url_path(scoped, "/service-resources", rid), headers=user, status=403)
    with sqlite3.connect(directory / "runtime.db") as db:
        assert db.execute(f"SELECT count(*) FROM {ROUTING_SCOPE_TABLE}").fetchone()[0] == 1
    call("DELETE", append_url_path(scoped, "/service-resources", rid))
    with sqlite3.connect(directory / "runtime.db") as db:
        assert db.execute(f"SELECT count(*) FROM {ROUTING_SCOPE_TABLE}").fetchone()[0] == 0
    call("DELETE", append_url_path("/api/v1/service-config-templates", template["template_id"]))
    call("DELETE", "/api/v1/container-templates/auth-agent")

    call("DELETE", append_url_path(scoped, "/logging"))
    call("DELETE", scoped)
    call("GET", scoped, status=404)


def test_real_browser_login_and_authenticated_manager_navigation(services):
    from playwright.sync_api import expect, sync_playwright

    _, urls, password, directory = services
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = local_browser_context(browser)
        page = context.new_page()
        errors = []
        protected_requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: (
                protected_requests.append(request) if "/api/v1/instances" in request.url else None
            ),
        )
        page.goto(append_url_path(urls["manager_web"], "/auth"))
        page.locator("#login-username").fill("admin")
        page.locator("#login-password").fill(password)
        page.locator('button[type="submit"]').click()
        page.wait_for_url("**/manager**")
        with page.expect_response(
            lambda response: "/api/v1/instances" in response.url and response.status == 200
        ):
            page.goto(append_url_path(urls["manager_web"], "/manager/instances"))
        expect(page.locator("#login-password")).to_have_count(0)
        page.wait_for_function("Boolean(localStorage.getItem('openjiuwen_access_token'))")
        assert protected_requests
        assert all(
            request.headers.get("authorization", "").startswith("Bearer ")
            for request in protected_requests
        )
        assert not errors
        page.screenshot(path=str(directory / "manager-authenticated.png"), full_page=True)
        previous = page.evaluate("localStorage.getItem('openjiuwen_access_token')")
        expiry = jwt.decode(previous, options={"verify_signature": False})["exp"]
        time.sleep(max(0, expiry - time.time() + 0.2))
        with page.expect_response(
            lambda response: "/api/v1/instances" in response.url and response.status == 200
        ):
            page.reload()
        assert page.evaluate("localStorage.getItem('openjiuwen_access_token')") != previous
        assert not errors
        context.close()

        # A regular identity cannot navigate into management, even via a direct URL.
        context = local_browser_context(browser)
        page = context.new_page()
        page.goto(append_url_path(urls["manager_web"], "/auth"))
        page.locator("#login-username").fill("user1")
        page.locator("#login-password").fill(password)
        page.locator('button[type="submit"]').click()
        expect(page.get_by_role("heading", name="暂无可用 Agent", exact=True)).to_be_visible(
            timeout=15000
        )
        page.goto(append_url_path(urls["manager_web"], "/manager/instances"))
        expect(page.get_by_role("heading", name="暂无可用 Agent", exact=True)).to_be_visible(
            timeout=15000
        )
        assert "/manager" not in page.url
        page.screenshot(path=str(directory / "regular-user-boundary.png"), full_page=True)
        context.close()
        browser.close()


def test_real_access_expiry_refresh_rotation_and_logout(services):
    client, urls, password, _ = services
    session = login(client, urls["manager_web"], "admin", password)
    token = session["access_token"]
    # Unverified parsing is only to schedule the test wait; production verifies it.
    exp = jwt.decode(token, options={"verify_signature": False})["exp"]
    time.sleep(max(0, exp - time.time() + 0.2))
    response = client.get(
        append_url_path(urls["user_web"], "/manager-api/v1/instances/"),
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status_code == 401
    refreshed = client.post(
        append_url_path(urls["user_web"], "/idp/v1/auth/refresh"),
        json={"refresh_token": session["refresh_token"]},
    )
    assert refreshed.status_code == 200
    fresh = refreshed.json()
    assert (
        client.post(
            append_url_path(urls["manager_web"], "/idp/v1/auth/refresh"),
            json={"refresh_token": session["refresh_token"]},
        ).status_code
        == 401
    )
    assert (
        client.get(
            append_url_path(urls["manager_web"], "/api/v1/instances/"),
            headers={"Authorization": "Bearer " + fresh["access_token"]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            append_url_path(urls["manager_web"], "/idp/v1/auth/logout"),
            json={"refresh_token": fresh["refresh_token"]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            append_url_path(urls["manager_web"], "/idp/v1/auth/refresh"),
            json={"refresh_token": fresh["refresh_token"]},
        ).status_code
        == 401
    )
    # Existing contract: logout revokes the refresh session, not a signed access
    # token. Access expires at its TTL; local role revocation is effective at once.
