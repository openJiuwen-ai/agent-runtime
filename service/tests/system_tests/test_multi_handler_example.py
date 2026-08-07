# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""End-to-end acceptance for the extensible multi-handler example."""

import base64
import hashlib
import json
import re
import runpy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
OAUTH_REDIRECT_URI = "http://testserver/docs/oauth2-redirect"
PKCE_VERIFIER = "example-verifier-abcdefghijklmnopqrstuvwxyz-0123456789"


def _envelope(msg_type: str, rawdata=None, request_id="request-1") -> dict:
    return {
        "type": msg_type,
        "metadata": {"request_id": request_id},
        "rawdata": rawdata or {},
        "version": "1",
    }


def _authorization_request(client: TestClient) -> str:
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(PKCE_VERIFIER.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    response = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "swagger-docs",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "state": "test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 200
    match = re.search(r'name="authorization_request_id" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _exchange_redirect(client: TestClient, redirect_url: str) -> str:
    query = parse_qs(urlsplit(redirect_url).query)
    assert query["state"] == ["test-state"]
    response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "client_id": "swagger-docs",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "code_verifier": PKCE_VERIFIER,
        },
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _local_login(client: TestClient) -> str:
    request_id = _authorization_request(client)
    response = client.post(
        "/auth/local/login",
        data={
            "authorization_request_id": request_id,
            "username": "demo",
            "password": "demo",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return _exchange_redirect(client, response.headers["location"])


@pytest.mark.system
def test_multi_handler_example_docs_oauth_crud_extension_and_stream(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "FEDERATED_AUTH_DATABASE_PATH", str(tmp_path / "federated-auth.db")
    )
    monkeypatch.syspath_prepend(str(EXAMPLES_DIR))
    namespace = runpy.run_path(str(EXAMPLES_DIR / "multi_handler_app.py"))

    app = namespace["app"]
    with TestClient(app.asgi) as client:
        docs = client.get("/docs")
        assert docs.status_code == 200
        assert '"clientId": "swagger-docs"' in docs.text
        assert '"usePkceWithAuthorizationCodeGrant": true' in docs.text

        spec = client.get("/openapi.json").json()
        expected_paths = {
            "/api/users.create",
            "/api/users.list",
            "/api/users.get",
            "/api/users.remove",
            "/api/chat",
            "/api/ping",
            "/api/identity.me",
            "/api/demo.error",
            "/api/custom.uppercase",
            "/health",
            "/oauth/authorize",
            "/oauth/token",
            "/auth/local/login",
            "/auth/federation/{connection_id}/login",
            "/auth/federation/{connection_id}/callback",
            "/demo-enterprise-idp/login",
        }
        assert expected_paths.issubset(spec["paths"])
        assert "OAuth2AuthorizationCode" in spec["components"]["securitySchemes"]
        assert spec["components"]["securitySchemes"]["OAuth2AuthorizationCode"] == {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "/oauth/authorize",
                    "scopes": {},
                    "tokenUrl": "/oauth/token",
                }
            },
        }
        assert spec["paths"]["/api/users.create"]["post"]["security"]
        schemas = spec["components"]["schemas"]
        create_input = schemas["UsersCreateEnvelope"]["properties"]["rawdata"]
        assert create_input["title"] == "CreateUserInput"
        assert create_input["properties"]["name"]["minLength"] == 1
        create_response = spec["paths"]["/api/users.create"]["post"]["responses"][
            "200"
        ]["content"]["application/json"]["schema"]
        assert create_response == {
            "$ref": "#/components/schemas/UsersCreateResponseEnvelope"
        }
        assert schemas["UsersCreateResponseEnvelope"]["properties"]["rawdata"] == {
            "$ref": "#/components/schemas/CreatedUserOutput"
        }

        denied = client.post("/api/ping", json=_envelope("ping"))
        assert denied.status_code == 401
        assert client.get("/health").status_code == 200

        invalid_token = client.post(
            "/api/ping",
            headers={"Authorization": "Bearer invalid"},
            json=_envelope("ping"),
        )
        assert invalid_token.status_code == 401

        wrong_login = client.post(
            "/auth/local/login",
            data={
                "authorization_request_id": _authorization_request(client),
                "username": "demo",
                "password": "wrong",
            },
        )
        assert wrong_login.status_code == 401
        token = _local_login(client)
        headers = {"Authorization": f"Bearer {token}"}

        invalid = client.post(
            "/api/users.create",
            headers=headers,
            json=_envelope("users.create", {"name": ""}),
        )
        assert invalid.status_code == 400
        assert invalid.json()["error_code"] == "validation"

        created = client.post(
            "/api/users.create",
            headers=headers,
            json=_envelope("users.create", {"name": "alice"}),
        )
        assert created.status_code == 200
        assert created.json()["rawdata"] == {
            "id": 1,
            "name": "alice",
            "created_by": "demo",
        }

        listed = client.post(
            "/api/users.list", headers=headers, json=_envelope("users.list")
        )
        assert listed.json()["rawdata"]["total"] == 1

        fetched = client.post(
            "/api/users.get",
            headers=headers,
            json=_envelope("users.get", {"id": 1}),
        )
        assert fetched.json()["rawdata"] == {"id": 1, "name": "alice"}

        extension = client.post(
            "/api/custom.uppercase",
            headers=headers,
            json=_envelope("custom.uppercase", {"text": "hello"}),
        )
        assert extension.json()["rawdata"] == {
            "text": "HELLO",
            "authenticated_user": "demo",
        }

        stream = client.post(
            "/api/chat",
            headers=headers,
            json=_envelope("chat", {"text": "hi"}),
        )
        chunks = [
            json.loads(line.removeprefix("data: "))
            for line in stream.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [chunk["rawdata"]["chunk"] for chunk in chunks] == ["h", "i"]
        assert chunks[-1]["is_final"] is True

        removed = client.post(
            "/api/users.remove",
            headers=headers,
            json=_envelope("users.remove", {"id": 1}),
        )
        assert removed.json()["rawdata"] == {"removed": True}

        missing = client.post(
            "/api/users.get",
            headers=headers,
            json=_envelope("users.get", {"id": 1}),
        )
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "not_found"

        error = client.post(
            "/api/demo.error",
            headers=headers,
            json=_envelope("demo.error", {"message": "bad request"}),
        )
        assert error.status_code == 400
        assert error.json()["error_code"] == "validation"


@pytest.mark.system
def test_multi_handler_example_federated_login_creates_stable_sqlite_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "FEDERATED_AUTH_DATABASE_PATH", str(tmp_path / "federated-auth.db")
    )
    monkeypatch.syspath_prepend(str(EXAMPLES_DIR))
    namespace = runpy.run_path(str(EXAMPLES_DIR / "multi_handler_app.py"))

    app = namespace["app"]
    with TestClient(app.asgi) as client:
        first_user_id = _federated_login_and_read_identity(client)
        second_user_id = _federated_login_and_read_identity(client)

    assert second_user_id == first_user_id


def _federated_login_and_read_identity(client: TestClient) -> str:
    request_id = _authorization_request(client)
    login = client.get(
        "/auth/federation/enterprise-demo/login",
        params={"authorization_request_id": request_id},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"].startswith("/demo-enterprise-idp/login?")

    idp_page = client.get(login.headers["location"])
    assert idp_page.status_code == 200
    assert "No SAML XML is accepted or verified here" in idp_page.text

    callback = client.post(
        "/auth/federation/enterprise-demo/callback",
        data={
            "authorization_request_id": request_id,
            "employee_id": "employee-10086",
            "display_name": "Enterprise Alice",
            "email": "alice@enterprise.example",
        },
        follow_redirects=False,
    )
    assert callback.status_code == 303
    token = _exchange_redirect(client, callback.headers["location"])

    identity = client.post(
        "/api/identity.me",
        headers={"Authorization": f"Bearer {token}"},
        json=_envelope("identity.me"),
    )
    assert identity.status_code == 200
    principal = identity.json()["rawdata"]
    assert principal["organization_id"] == "virtual-org-enterprise-demo"
    assert principal["display_name"] == "Enterprise Alice"
    assert principal["roles"] == ["member"]
    assert principal["auth_source"] == "saml"
    return principal["user_id"]
