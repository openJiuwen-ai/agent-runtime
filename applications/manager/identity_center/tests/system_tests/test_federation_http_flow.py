"""System test for the browser federation flow and local token boundary."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from identity_center.app import create_app
from identity_center.infrastructure.config import settings


def _query_value(url: str, name: str) -> str:
    values = parse_qs(urlsplit(url).query).get(name)
    assert values
    return values[0]


def test_federation_http_flow_issues_normal_identity_token(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_type", "sqlite")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "identity.db"))
    monkeypatch.setattr(settings, "federation_demo_enabled", True)
    monkeypatch.setattr(settings, "federation_public_path_prefix", "")
    monkeypatch.setattr(settings, "seed_admin", False)
    monkeypatch.setattr(settings, "seed_user1", False)

    with TestClient(create_app()) as client:
        connections = client.get("/v1/auth/federation/connections")
        assert connections.status_code == 200
        assert connections.json() == {
            "connections": [
                {
                    "connection_id": "enterprise-demo",
                    "name": "Enterprise Demo SSO",
                }
            ]
        }

        started = client.get(
            "/v1/auth/federation/enterprise-demo/login",
            params={"return_to": "/auth"},
            follow_redirects=False,
        )
        assert started.status_code == 303
        upstream_url = started.headers["location"]
        assert upstream_url.startswith("/v1/auth/federation/demo/idp/login?")

        provider_page = client.get(upstream_url)
        assert provider_page.status_code == 200
        assert "Local simulation only" in provider_page.text

        callback = client.post(
            "/v1/auth/federation/enterprise-demo/callback",
            data={
                "authorization_request_id": _query_value(
                    upstream_url,
                    "authorization_request_id",
                ),
                "connection_id": "enterprise-demo",
                "employee_id": "employee-http-10086",
                "display_name": "HTTP Enterprise User",
                "email": "http-user@example.test",
                "groups": "enterprise-admins",
                "is_admin": "false",
            },
            follow_redirects=False,
        )
        assert callback.status_code == 303
        code = _query_value(callback.headers["location"], "federation_code")

        exchanged = client.post(
            "/v1/auth/federation/exchange",
            json={"code": code},
        )
        assert exchanged.status_code == 200
        token = exchanged.json()["access_token"]

        replay = client.post(
            "/v1/auth/federation/exchange",
            json={"code": code},
        )
        assert replay.status_code == 401

        me = client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.status_code == 200
        assert me.json()["display_name"] == "HTTP Enterprise User"
        assert me.json()["is_admin"] is True
        assert me.json()["groups"] == ["federated-enterprise-demo"]

        admin_api = client.get(
            "/v1/users/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert admin_api.status_code == 200

        started_again = client.get(
            "/v1/auth/federation/enterprise-demo/login",
            params={"return_to": "/auth"},
            follow_redirects=False,
        )
        callback_again = client.post(
            "/v1/auth/federation/enterprise-demo/callback",
            data={
                "authorization_request_id": _query_value(
                    started_again.headers["location"],
                    "authorization_request_id",
                ),
                "connection_id": "enterprise-demo",
                "employee_id": "employee-http-10086",
                "display_name": "HTTP Enterprise User",
                "email": "http-user@example.test",
                "groups": "employees",
                "is_admin": "true",
                "role": "admin",
            },
            follow_redirects=False,
        )
        downgraded = client.post(
            "/v1/auth/federation/exchange",
            json={
                "code": _query_value(
                    callback_again.headers["location"],
                    "federation_code",
                )
            },
        )
        downgraded_token = downgraded.json()["access_token"]
        downgraded_me = client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {downgraded_token}"},
        )
        assert downgraded_me.status_code == 200
        assert downgraded_me.json()["user_id"] == me.json()["user_id"]
        assert downgraded_me.json()["is_admin"] is False
        denied_admin_api = client.get(
            "/v1/users/",
            headers={"Authorization": f"Bearer {downgraded_token}"},
        )
        assert denied_admin_api.status_code == 403
