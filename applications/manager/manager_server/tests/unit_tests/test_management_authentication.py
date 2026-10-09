"""Final-route coverage and real signed JWT / local role boundary tests.

Only the database and Identity public-key transport are supplied by this fixture;
authentication and authorization dependencies are never overridden.
"""

from __future__ import annotations

import re
import time
from datetime import timedelta

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler

from manager_server.core.authz import AuthzService
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.infrastructure.utils import utc_now
from manager_server.models.authz_models import AUTHZ_ROLE_USER_TABLE_DEF
from manager_server.models.table_init import init_all_tables
from manager_server.routers.auth_guards import get_current_user, require_admin
from manager_server.routers.register import router_register
from manager_server.security import jwt_verify

PUBLIC = {
    ("GET", "/"),
    ("GET", "/api/health"),
    ("GET", "/api/manager-ws/status"),
    ("DELETE", "/api/v1/user-console/active-cluster"),
}
# Each exception needs an explicit ownership/admission regression test. New
# token-only management routes must not silently pass the coverage assertion.
USER_SCOPED = {
    ("GET", "/api/v1/authz/me"),
    ("GET", "/api/v1/user-console/agent-contexts"),
    ("POST", "/api/v1/user-console/active-cluster"),
    ("GET", "/api/v1/user-console/user-face-upstream"),
    ("POST", "/api/v1/user-console/workspace/expand-requests"),
    ("GET", "/api/v1/user-console/approvals/candidates"),
    ("GET", "/api/v1/user-console/approvals/mine"),
    ("POST", "/api/v1/user-console/approvals/{order_num}/cancel"),
}
LEGACY_MODULES = {
    "template_routers",
    "import_export_routers",
    "instance_resource_routers",
    "instance_routers",
    "application_config_routers",
    "instance_access_routers",
}


def make_app():
    app = FastAPI()
    router_register(app)
    return app


def calls(dependant):
    yield dependant.call
    for child in dependant.dependencies:
        yield from calls(child)


def routes(app):
    return [route for route in app.routes if isinstance(route, APIRoute)]


def assert_route_policy(app):
    observed = set()
    for route in routes(app):
        dependencies = set(calls(route.dependant))
        permissions = {getattr(call, "required_permission", None) for call in dependencies} - {None}
        for method in route.methods:
            key = (method, route.path)
            assert key not in observed, f"duplicate route: {key}"
            observed.add(key)
            if key in PUBLIC:
                assert get_current_user not in dependencies, key
                assert require_admin not in dependencies and not permissions, key
                continue
            assert get_current_user in dependencies, f"missing authentication: {key}"
            if key in USER_SCOPED:
                assert require_admin not in dependencies and not permissions, key
            elif route.endpoint.__module__.rsplit(".", 1)[-1] in LEGACY_MODULES:
                assert require_admin in dependencies, f"missing legacy admin guard: {key}"
            else:
                assert permissions or require_admin in dependencies, (
                    f"missing authorization policy: {key}"
                )
                if permissions:
                    assert require_admin not in dependencies, (
                        f"permission API under admin subtree: {key}"
                    )
                    module = route.endpoint.__module__.rsplit(".", 1)[-1]
                    expected = {
                        "authz_routers": "iam:role:read" if method == "GET" else "iam:role:write",
                        "quota_routers": "quota:read" if method == "GET" else "quota:write",
                        "approval_routers": "approval:read" if method == "GET" else "approval:act",
                    }.get(module)
                    if expected:
                        assert permissions == {expected}, f"wrong permission for method: {key}"
    assert PUBLIC | USER_SCOPED <= observed


def test_final_route_authentication_and_authorization_coverage():
    assert_route_policy(make_app())


def test_application_factories_do_not_accumulate_routes():
    first, second = make_app(), make_app()
    assert [(r.path, r.methods) for r in routes(first)] == [
        (r.path, r.methods) for r in routes(second)
    ]
    assert_route_policy(second)


def test_coverage_rejects_future_unclassified_routes():
    app = make_app()

    @app.get("/api/v1/future")
    async def future():
        return {}

    with pytest.raises(AssertionError, match="missing authentication"):
        assert_route_policy(app)

    app.routes.pop()
    app.add_api_route(
        "/api/v1/future", future, methods=["GET"], dependencies=[Depends(get_current_user)]
    )
    with pytest.raises(AssertionError, match="missing authorization policy"):
        assert_route_policy(app)


@pytest.fixture(name="signing_key", scope="module")
def signing_key_fixture():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, subject="regular", **changes):
    now = int(time.time())
    claims = {
        "sub": subject,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": now + 600,
        "iat": now,
        "typ": "access",
        "groups": [],
        "is_admin": False,
    }
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256")


@pytest_asyncio.fixture(name="secure_api")
async def secure_api_fixture(tmp_path, monkeypatch, signing_key):
    handler = SQLiteHandler(str(tmp_path / "security.db"))
    await handler.init_database()
    await handler.connect()
    await init_all_tables(handler)
    app = make_app()

    def current_db_handler():
        return handler

    app.dependency_overrides[get_db_handler] = current_db_handler
    pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setattr(jwt_verify, "_public_pem", pem)
    monkeypatch.setattr(settings, "workspace_quota_enabled", True)

    async def unexpected_push(*args, **kwargs):
        pytest.fail("denied requests must not reach Gateway/Runtime transport")

    monkeypatch.setattr("manager_server.manager_config_push.client.http_request", unexpected_push)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        yield client, handler
    await handler.disconnect()


def protected_route_cases():
    cases = []
    for route in routes(make_app()):
        is_legacy = require_admin in set(calls(route.dependant))
        for method in route.methods:
            if (method, route.path) not in PUBLIC:
                cases.append((method, route.path, is_legacy))
    return cases


PROTECTED_ROUTES = protected_route_cases()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,is_legacy", PROTECTED_ROUTES)
async def test_all_business_routes_reject_missing_and_invalid_tokens(
    secure_api, method, path, is_legacy
):
    client, _ = secure_api
    url = re.sub(r"\{[^}]+\}", "security-missing", path)
    # Even an invalid business body must not run a handler before authentication.
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        response = await client.request(method, url, headers=headers, json={})
        assert response.status_code == 401, (method, url, response.text)
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [(m, p) for m, p, legacy in PROTECTED_ROUTES if legacy])
async def test_all_legacy_routes_reject_identity_admin_without_manager_role(
    secure_api, signing_key, method, path
):
    client, _ = secure_api
    response = await client.request(
        method,
        re.sub(r"\{[^}]+\}", "security-missing", path),
        json={},
        headers={"Authorization": "Bearer " + token(signing_key, is_admin=True)},
    )
    assert response.status_code == 403, (method, path, response.text)


@pytest.mark.asyncio
async def test_local_roles_not_identity_admin_and_permission_boundaries(secure_api, signing_key):
    client, handler = secure_api
    service = AuthzService(handler)
    await service.create_role(
        role_id="reader",
        name="Reader",
        description=None,
        scope="admin",
        permission_ids=["quota:read"],
        operator_id="admin",
    )
    await service.assign_role_users("reader", ["regular"], "admin")
    headers = {"Authorization": "Bearer " + token(signing_key)}
    # Preserve current contract: any enabled admin-scope role allows legacy APIs,
    # but does NOT automatically acquire the fine-grained write/IAM permissions.
    assert (await client.get("/api/v1/instances/", headers=headers)).status_code == 200
    assert (
        await client.get("/api/v1/instances/missing/workspace-quota/policies", headers=headers)
    ).status_code == 200
    for method, path in (
        ("POST", "/api/v1/instances/missing/workspace-quota/policies"),
        ("GET", "/api/v1/authz/roles"),
        ("GET", "/api/v1/approvals"),
    ):
        assert (await client.request(method, path, json={}, headers=headers)).status_code == 403
    assert (
        await client.post(
            "/api/v1/user-console/active-cluster",
            json={"jiuwenclaw_id": "missing"},
            headers=headers,
        )
    ).status_code == 403
    await service.update_role(
        "reader",
        name=None,
        description=None,
        scope=None,
        enabled=False,
        permission_ids=None,
        operator_id="admin",
    )
    assert (await client.get("/api/v1/instances/", headers=headers)).status_code == 403
    await service.update_role(
        "reader",
        name=None,
        description=None,
        scope=None,
        enabled=True,
        permission_ids=None,
        operator_id="admin",
    )
    await handler.update(
        AUTHZ_ROLE_USER_TABLE_DEF.table_name,
        {"role_id": "reader", "user_id": "regular"},
        {"expires_at": utc_now() - timedelta(seconds=1)},
    )
    assert (await client.get("/api/v1/instances/", headers=headers)).status_code == 403
    await service.replace_role_users("reader", [], "admin")
    assert (await client.get("/api/v1/instances/", headers=headers)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "iss", "aud", "typ"])
async def test_required_access_claims(secure_api, signing_key, claim):
    client, _ = secure_api
    claims = jwt.decode(token(signing_key), options={"verify_signature": False})
    claims.pop(claim)
    invalid = jwt.encode(claims, signing_key, algorithm="RS256")
    assert (
        await client.get("/api/v1/authz/me", headers={"Authorization": "Bearer " + invalid})
    ).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"exp": 1},
        {"iat": int(time.time()) + 86400},
        {"iss": "wrong"},
        {"aud": "wrong"},
        {"sub": ""},
        {"sub": "  "},
        {"sub": 1},
        {"typ": "refresh"},
        {"groups": "admin"},
        {"groups": [1]},
        {"exp": None},
        {"iat": []},
        {"exp": True},
        {"exp": "12345678900"},
    ],
)
async def test_invalid_claims(secure_api, signing_key, changes):
    client, _ = secure_api
    response = await client.get(
        "/api/v1/authz/me", headers={"Authorization": "Bearer " + token(signing_key, **changes)}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_signature_rotation_and_public_key_outage(secure_api, monkeypatch):
    client, _ = secure_api
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fetches = []

    async def fetch():
        fetches.append(True)
        return rotated.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    monkeypatch.setattr(jwt_verify, "_fetch_public_pem", fetch)
    headers = {"Authorization": "Bearer " + token(rotated)}
    assert (await client.get("/api/v1/authz/me", headers=headers)).status_code == 200
    assert len(fetches) == 1

    async def unavailable():
        raise jwt_verify.PublicKeyUnavailableError("test outage")

    monkeypatch.setattr(jwt_verify, "_public_pem", None)
    monkeypatch.setattr(jwt_verify, "_fetch_public_pem", unavailable)
    assert (await client.get("/api/v1/instances/", headers=headers)).status_code == 503


@pytest.mark.asyncio
async def test_public_logout_cleanup_works_with_expired_token(secure_api, signing_key):
    client, _ = secure_api
    headers = {"Authorization": "Bearer " + token(signing_key, exp=1)}
    response = await client.delete("/api/v1/user-console/active-cluster", headers=headers)
    assert response.status_code == 200
    assert "jiuwenclaw_id=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert (await client.get("/api/health", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_forged_signature_and_wrong_algorithm_are_rejected(
    secure_api, signing_key, monkeypatch
):
    client, _ = secure_api
    original = await jwt_verify.get_public_pem()

    async def unchanged_key():
        return original

    monkeypatch.setattr(jwt_verify, "_fetch_public_pem", unchanged_key)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(token(signing_key), options={"verify_signature": False})
    for invalid in (
        token(other_key),
        jwt.encode(claims, "not-an-rsa-key", algorithm="HS256"),
        jwt.encode(claims, None, algorithm="none"),
    ):
        assert (
            await client.get("/api/v1/instances/", headers={"Authorization": "Bearer " + invalid})
        ).status_code == 401


@pytest.mark.asyncio
async def test_cookie_or_identity_header_cannot_replace_bearer(secure_api, signing_key):
    client, _ = secure_api
    client.cookies.set("openjiuwen_access_token", token(signing_key, "admin"))
    response = await client.get(
        "/api/v1/instances/", headers={"X-User-Id": "admin", "X-Is-Admin": "true"}
    )
    assert response.status_code == 401
