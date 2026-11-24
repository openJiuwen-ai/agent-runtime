"""Directory HTTP guards are independent of the user authentication endpoints."""

from functools import partial
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import identity_center.app as identity_app
from identity_center.core.authorization import DirectoryAuthorizationClient
from identity_center.infrastructure.config import settings
from identity_center.security.tokens import decode_access_token


@pytest.fixture
def directory_client(tmp_path, monkeypatch):
    password = "directory-test-password"
    monkeypatch.setattr(settings, "db_type", "sqlite")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "identity.db"))
    monkeypatch.setattr(settings, "federation_demo_enabled", False)
    monkeypatch.setattr(settings, "seed_admin", True)
    monkeypatch.setattr(settings, "seed_user1", True)
    monkeypatch.setattr(settings, "admin_password", password)
    monkeypatch.setattr(settings, "user1_password", password)
    monkeypatch.setattr(settings, "manager_base_url", "https://authorization.test")
    context = SimpleNamespace(decisions={"admin": True, "user1": False}, calls=[], status=200)

    def authorize(request):
        assert str(request.url) == "https://authorization.test/api/v1/authz/me"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        user_id = decode_access_token(token)["sub"]
        context.calls.append(user_id)
        return httpx.Response(context.status, json={
            "code": 200,
            "data": {"user_id": user_id, "is_platform_admin": context.decisions.get(user_id, False)},
        })

    monkeypatch.setattr(identity_app, "DirectoryAuthorizationClient", partial(
        DirectoryAuthorizationClient, transport=httpx.MockTransport(authorize),
    ))
    with TestClient(identity_app.create_app()) as client:
        context.client = client
        context.headers = {}
        context.bundles = {}
        for name, surface in [("admin", "manager"), ("user1", "manager"), ("user1", "user")]:
            response = client.post(
                "/v1/auth/token", data={"username": name, "password": password},
                headers={"X-Jiuwen-Web-Surface": surface},
            )
            assert response.status_code == 200
            key = name + "_" + surface
            context.bundles[key] = response.json()
            context.headers[key] = {"Authorization": "Bearer " + response.json()["access_token"]}
        yield context


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/v1/users/", None),
    ("POST", "/v1/users/", {"username": "B", "password": "B-password", "display_name": "B"}),
    ("PATCH", "/v1/users/admin", {"display_name": "B"}),
    ("DELETE", "/v1/users/admin", None),
    ("PUT", "/v1/users/admin/orgs", {"group_ids": []}),
    ("GET", "/v1/orgs/", None),
    ("POST", "/v1/orgs/", {"group_id": "B", "display_name": "B"}),
    ("PATCH", "/v1/orgs/B", {"display_name": "C"}),
    ("DELETE", "/v1/orgs/B", None),
    ("POST", "/v1/orgs/B/members", {"user_ids": ["admin"]}),
])
def test_ordinary_manager_session_cannot_manage_directory(directory_client, method, path, body):
    ctx = directory_client
    response = ctx.client.request(method, path, json=body, headers=ctx.headers["user1_manager"])
    assert response.status_code == 403
    assert ctx.calls == ["user1"]


def test_administrator_can_create_modify_and_delete_directory_records(directory_client):
    ctx = directory_client
    headers = ctx.headers["admin_manager"]
    assert ctx.client.post("/v1/users/", headers=headers, json={
        "username": "B", "password": "B-password", "display_name": "B",
    }).status_code == 200
    assert ctx.client.patch("/v1/users/B", headers=headers, json={"display_name": "C"}).status_code == 200
    assert ctx.client.get("/v1/users/B", headers=headers).json()["display_name"] == "C"
    assert ctx.client.post("/v1/orgs/", headers=headers, json={
        "group_id": "B", "display_name": "B",
    }).status_code == 200
    assert ctx.client.patch("/v1/orgs/B", headers=headers, json={"display_name": "C"}).status_code == 200
    assert ctx.client.get("/v1/orgs/B", headers=headers).json()["display_name"] == "C"
    assert ctx.client.delete("/v1/users/B", headers=headers).status_code == 200
    assert ctx.client.delete("/v1/orgs/B", headers=headers).status_code == 200


def test_role_removal_applies_to_the_existing_session(directory_client):
    ctx = directory_client
    ctx.decisions["user1"] = True
    assert ctx.client.get("/v1/users/", headers=ctx.headers["user1_manager"]).status_code == 200
    ctx.decisions["user1"] = False
    assert ctx.client.get("/v1/users/", headers=ctx.headers["user1_manager"]).status_code == 403
    assert ctx.calls == ["user1", "user1"]


def test_disabled_account_is_rejected_before_the_authorization_query(directory_client):
    ctx = directory_client
    assert ctx.client.patch("/v1/users/user1", headers=ctx.headers["admin_manager"], json={
        "status": "disabled",
    }).status_code == 200
    ctx.calls.clear()
    assert ctx.client.get("/v1/users/", headers=ctx.headers["user1_manager"]).status_code == 401
    assert ctx.calls == []


def test_authorization_outage_does_not_affect_user_identity_or_refresh(directory_client):
    ctx = directory_client
    ctx.status = 503
    user_headers = ctx.headers["user1_user"]
    for path in ["/v1/auth/me", "/v1/auth/me/orgs", "/v1/auth/user-session", "/v1/auth/public_key"]:
        assert ctx.client.get(path, headers=user_headers).status_code == 200
    refresh = ctx.client.post("/v1/auth/refresh?surface=user", json={
        "refresh_token": ctx.bundles["user1_user"]["refresh_token"],
    })
    assert refresh.status_code == 200
    assert decode_access_token(refresh.json()["access_token"])["surface"] == "user"
    assert ctx.client.get("/v1/users/", headers=user_headers).status_code == 403
    assert ctx.calls == []
    assert ctx.client.get("/v1/users/", headers=ctx.headers["admin_manager"]).status_code == 503
    assert ctx.calls == ["admin"]
