"""OIDC trust boundaries using real RSA JWTs and transactional SQLite state."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from functools import partial
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from identity_center.core.federation import IdentityFederationService
from identity_center.core.federation import connections as federation_connections
from identity_center.core.federation.oidc_provider import (
    OidcFederationProvider,
    ValidatedOidcCallback,
)
from identity_center.infrastructure.config import Settings
from identity_center.models.table_init import init_all_tables
from identity_center.security.jwt_keys import load_signing_key
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.service import FederationError

ISSUER = "https://sso.example.test/realms/customer"
CALLBACK = "https://app.example.test/idp/v1/auth/federation/keycloak/callback"
BROWSER = "a-browser-binding-with-at-least-43-characters-01"


def _settings(**overrides):
    values = {
        "IDENTITY_FEDERATION_DEMO_ENABLED": False,
        "IDENTITY_FEDERATION_OIDC_ISSUER": ISSUER,
        "IDENTITY_FEDERATION_OIDC_CLIENT_ID": "identity-center",
        "IDENTITY_FEDERATION_OIDC_CLIENT_SECRET": "unit-test-client-secret",
        "IDENTITY_FEDERATION_OIDC_REDIRECT_URI": CALLBACK,
        "IDENTITY_SEED_ADMIN": False,
        "IDENTITY_SEED_USER1": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _endpoint(name):
    return urljoin(f"{ISSUER}/", name)


@pytest_asyncio.fixture(name="runtime")
async def runtime_fixture(tmp_path, monkeypatch):
    handler = SQLiteHandler(str(tmp_path / "identity.db"))
    await handler.init_database()
    await handler.connect()
    await init_all_tables(handler)
    await load_signing_key(handler)
    rt = SimpleNamespace(
        handler=handler,
        service=None,
        provider=None,
        key=rsa.generate_private_key(public_exponent=65537, key_size=2048),
        kid="key-1",
        codes={},
        mutations={},
        calls=[],
        metadata={},
        token_entered=None,
        token_release=None,
        bundle_override=None,
    )

    async def upstream(request):
        rt.calls.append(request.url.path)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json=dict(
                    {
                        "issuer": ISSUER,
                        "authorization_endpoint": _endpoint("auth"),
                        "token_endpoint": _endpoint("token"),
                        "jwks_uri": _endpoint("certs"),
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["client_secret_post"],
                    },
                    **rt.metadata,
                ),
            )
        if request.url.path.endswith("/certs"):
            key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rt.key.public_key()))
            return httpx.Response(
                200, json={"keys": [dict(key, kid=rt.kid, use="sig", alg="RS256")]}
            )
        if request.url.path.endswith("/token"):
            if rt.token_entered is not None:
                rt.token_entered.set()
                await rt.token_release.wait()
            fields = parse_qs(request.content.decode())
            assert fields["redirect_uri"] == [CALLBACK]
            assert fields["client_id"] == ["identity-center"]
            assert fields["client_secret"] == ["unit-test-client-secret"]
            context = rt.codes.pop(fields["code"][0])
            assert fields["code_verifier"] == [context["oidc_verifier"]]
            bundle = rt.bundle_override
            if bundle is None:
                bundle = _bundle(rt, context["oidc_nonce"])
            return httpx.Response(200, json=bundle)
        raise AssertionError("unexpected upstream endpoint")

    client_factory = partial(
        httpx.AsyncClient, transport=httpx.MockTransport(upstream)
    )
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    rt.provider = OidcFederationProvider(handler, _settings())
    rt.service = await IdentityFederationService.create(handler, _settings())
    try:
        yield rt
    finally:
        await rt.service.close()
        await rt.provider.close()
        await handler.disconnect()


def _bundle(rt, expected_nonce, *, algorithm="RS256", key=None, **changes):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "identity-center",
        "sub": "subject-1",
        "nonce": expected_nonce,
        "exp": now + 300,
        "iat": now,
        "preferred_username": "alice",
        "email": "alice@example.test",
        "groups": ["enterprise-admins"],
    }
    claims.update(rt.mutations)
    claims.update(changes)
    return {
        "id_token": jwt.encode(
            claims, key or rt.key, algorithm=algorithm, headers={"kid": rt.kid}
        ),
        "access_token": jwt.encode(
            {
                "iss": ISSUER,
                "aud": "egress_gateway",
                "sub": claims["sub"],
                "azp": "identity-center",
                "iat": now,
                "exp": now + 86400,
            },
            rt.key,
            algorithm="RS256",
            headers={"kid": rt.kid},
        ),
        "refresh_token": "never-deliver-upstream-refresh",
        "token_type": "Bearer",
    }


async def _begin(rt):
    url = await rt.service.begin_login("keycloak", "/auth", browser_binding=BROWSER)
    query = parse_qs(urlsplit(url).query)
    state = query["state"][0]
    row = await rt.handler.get("federation_login_state", {"request_id": state})
    code = "upstream-code-" + state
    rt.codes[code] = row.data
    return url, {"state": state, "code": code, "iss": ISSUER}, row.data


async def _consume_provider_bundle(rt, bundle, nonce):
    context = {"oidc_nonce": nonce, "oidc_verifier": "unit-test-pkce-verifier"}
    code = "unit-test-provider-code"
    rt.codes[code] = context
    rt.bundle_override = bundle
    callback = ValidatedOidcCallback(
        {"state": "unit-test-provider-state", "code": code, "iss": ISSUER}, context
    )
    return await rt.provider.consume_callback(rt.service.connections[0], callback)


@pytest.mark.asyncio
async def test_browser_binding_pkce_single_use_and_stable_subject(runtime):
    rt = runtime
    url, callback, data = await _begin(rt)
    query = parse_qs(urlsplit(url).query)
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(data["oidc_verifier"].encode()).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert query["code_challenge"] == [challenge] and query[
        "code_challenge_method"
    ] == ["S256"]
    assert data["oidc_verifier"] not in url
    assert BROWSER not in json.dumps(data)
    before = list(rt.calls)
    for binding in (None, "different-browser"):
        with pytest.raises(FederationError, match="browser"):
            await rt.service.complete_callback(
                "keycloak", callback, browser_binding=binding
            )
    assert rt.calls == before
    target, principal = await rt.service.complete_callback(
        "keycloak", callback, browser_binding=BROWSER
    )
    code = parse_qs(urlsplit(target).query)["federation_code"][0]
    assert await rt.service.exchange_code(code) == "invalid_federation_code"
    assert isinstance(
        await rt.service.exchange_code(code, browser_binding=BROWSER), dict
    )
    assert (
        await rt.service.exchange_code(code, browser_binding=BROWSER)
        == "invalid_federation_code"
    )
    with pytest.raises(FederationError, match="missing or expired"):
        await rt.service.complete_callback(
            "keycloak", callback, browser_binding=BROWSER
        )
    rt.mutations["preferred_username"] = "alice-renamed"
    _, callback, _ = await _begin(rt)
    _, again = await rt.service.complete_callback(
        "keycloak", callback, browser_binding=BROWSER
    )
    assert principal.user_id == again.user_id
    user = await rt.handler.get("identity_user", {"user_id": principal.user_id})
    assert not user.is_admin
    row = (await rt.handler.list_records("federated_identity", {}, limit=10, offset=0))[
        0
    ]
    assert row.attributes["preferred_username"] == "alice-renamed"
    assert "nonce" not in row.attributes and "access_token" not in row.attributes
    # Equal email/username with a different verified subject must not merge accounts.
    rt.mutations["sub"] = "subject-2"
    _, callback, _ = await _begin(rt)
    _, other = await rt.service.complete_callback(
        "keycloak", callback, browser_binding=BROWSER
    )
    assert other.user_id != principal.user_id


@pytest.mark.asyncio
async def test_parallel_callback_only_exchanges_upstream_code_once(runtime):
    rt = runtime
    _, callback, _ = await _begin(rt)
    rt.token_entered, rt.token_release = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(
        rt.service.complete_callback("keycloak", callback, browser_binding=BROWSER)
    )
    try:
        await asyncio.wait_for(rt.token_entered.wait(), timeout=3)
        with pytest.raises(FederationError, match="missing or expired"):
            await rt.service.complete_callback(
                "keycloak", callback, browser_binding=BROWSER
            )
    finally:
        rt.token_release.set()
    target, _ = await first
    assert "federation_code=" in target
    assert sum(path.endswith("/token") for path in rt.calls) == 1


@pytest.mark.asyncio
async def test_disabled_local_user_is_not_reactivated_by_sso(runtime):
    rt = runtime
    _, callback, _ = await _begin(rt)
    _, principal = await rt.service.complete_callback(
        "keycloak", callback, browser_binding=BROWSER
    )
    await rt.handler.update(
        "identity_user", {"user_id": principal.user_id}, {"status": "disabled"}
    )
    _, callback, _ = await _begin(rt)
    target, _ = await rt.service.complete_callback(
        "keycloak", callback, browser_binding=BROWSER
    )
    code = parse_qs(urlsplit(target).query)["federation_code"][0]
    assert await rt.service.exchange_code(code, browser_binding=BROWSER) == "disabled"
    user = await rt.handler.get("identity_user", {"user_id": principal.user_id})
    assert user.status == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://untrusted.example.test"},
        {"aud": "another-client"},
        {"nonce": "wrong-nonce"},
        {"exp": 1},
        {"iat": int(time.time()) + 120},
        {"azp": "another-client"},
        {"aud": ["identity-center", "another-client"]},
        {"sub": ""},
        {"at_hash": "forged-access-token-hash"},
    ],
)
async def test_id_token_trust_claims_rejected(runtime, changes):
    bundle = _bundle(runtime, "expected-nonce", **changes)
    with pytest.raises(FederationError, match="validation failed"):
        await _consume_provider_bundle(runtime, bundle, "expected-nonce")


@pytest.mark.asyncio
async def test_forged_signature_algorithm_and_rotation(runtime):
    rt = runtime
    await rt.provider.discovery()
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for bundle in (
        _bundle(rt, "n", key=other_key),
        _bundle(
            rt, "n", algorithm="HS256", key="untrusted-hmac-secret-more-than-32-bytes"
        ),
    ):
        with pytest.raises(FederationError):
            await _consume_provider_bundle(rt, bundle, "n")
    rt.key, rt.kid = other_key, "rotated-key-2"
    result = await _consume_provider_bundle(rt, _bundle(rt, "n"), "n")
    assert result.identity.external_subject == "subject-1"
    assert rt.calls.count(urlsplit(_endpoint("certs")).path) == 2


@pytest.mark.asyncio
async def test_expired_state_and_failed_token_do_not_create_users(runtime):
    rt = runtime
    _, callback, _ = await _begin(rt)
    from datetime import timedelta

    from identity_center.infrastructure.utils import utc_now

    await rt.handler.update(
        "federation_login_state",
        {"request_id": callback["state"]},
        {"expires_at": utc_now() - timedelta(seconds=1)},
    )
    with pytest.raises(FederationError, match="missing or expired"):
        await rt.service.complete_callback(
            "keycloak", callback, browser_binding=BROWSER
        )
    assert not any(path.endswith("/token") for path in rt.calls)
    _, callback, _ = await _begin(rt)
    rt.mutations["nonce"] = "forged-nonce"
    with pytest.raises(FederationError):
        await rt.service.complete_callback(
            "keycloak", callback, browser_binding=BROWSER
        )
    assert (
        await rt.handler.get(
            "federation_login_state", {"request_id": callback["state"]}
        )
        is None
    )
    assert not await rt.handler.list_records(
        "federated_identity", {}, limit=10, offset=0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"issuer": "https://another.example.test"},
        {"token_endpoint": "https://another.example.test/token"},
        {"jwks_uri": "http://127.0.0.1/certs"},
        {"code_challenge_methods_supported": ["plain"]},
    ],
)
async def test_discovery_cannot_change_trusted_issuer_or_secret_destination(
    runtime, metadata
):
    runtime.metadata.update(metadata)
    with pytest.raises(FederationError):
        await _begin(runtime)
    assert not any(path.endswith("/token") for path in runtime.calls)


@pytest.mark.asyncio
async def test_customer_access_token_is_sealed_until_browser_exchange(runtime):
    rt = runtime
    _, callback, _ = await _begin(rt)
    target, principal = await rt.service.complete_callback(
        "keycloak", callback, browser_binding=BROWSER
    )
    rows = await rt.handler.list_records(
        "federation_login_code", {}, limit=10, offset=0
    )
    persisted = json.dumps(rows[0].data)
    assert "never-deliver-upstream-refresh" not in persisted
    assert not rows[0].data["federation"]["access_token"].startswith("eyJ")
    code = parse_qs(urlsplit(target).query)["federation_code"][0]
    bundle = await rt.service.exchange_code(code, browser_binding=BROWSER)
    federation = bundle["federation"]
    verified = jwt.decode(
        federation["access_token"],
        rt.key.public_key(),
        algorithms=["RS256"],
        audience="egress_gateway",
        issuer=ISSUER,
    )
    assert verified["sub"] == "subject-1"
    assert federation["user_id"] == principal.user_id
    assert federation["token_type"] == "Bearer"
    assert "refresh_token" not in federation
    assert (
        verified["iss"]
        != jwt.decode(bundle["access_token"], options={"verify_signature": False})[
            "iss"
        ]
    )


@pytest.mark.asyncio
async def test_customer_token_without_exp_still_validates_issuer_and_audience(runtime):
    rt = runtime
    claims = {
        "iss": ISSUER,
        "aud": "egress_gateway",
        "sub": "subject-1",
        "azp": "identity-center",
        "iat": int(time.time()),
    }
    bundle = {
        "token_type": "Bearer",
        "access_token": jwt.encode(
            claims, rt.key, algorithm="RS256", headers={"kid": rt.kid}
        ),
    }
    verified = await rt.provider.validate_access_token(
        bundle, audience="egress_gateway", subject="subject-1"
    )
    assert "exp" not in verified
    with pytest.raises(FederationError, match="access token validation"):
        await rt.provider.validate_access_token(
            bundle, audience="different-egress", subject="subject-1"
        )


@pytest.mark.asyncio
async def test_customer_expired_token_is_not_treated_as_permanent(runtime):
    rt = runtime
    claims = {
        "iss": ISSUER,
        "aud": "egress_gateway",
        "sub": "subject-1",
        "azp": "identity-center",
        "iat": int(time.time()) - 100,
        "exp": int(time.time()) - 1,
    }
    bundle = {
        "token_type": "Bearer",
        "access_token": jwt.encode(
            claims, rt.key, algorithm="RS256", headers={"kid": rt.kid}
        ),
    }
    with pytest.raises(FederationError, match="access token validation"):
        await rt.provider.validate_access_token(
            bundle, audience="egress_gateway", subject="subject-1"
        )


@pytest_asyncio.fixture(name="browser_runtime")
async def browser_runtime_fixture(runtime, tmp_path):
    rt = runtime
    config = tmp_path / "connections.json"
    config.write_text(
        json.dumps(
            {
                "connections": [
                    {
                        "connection_id": "keycloak",
                        "name": "Enterprise",
                        "flow": "browser_pkce",
                        "issuer": ISSUER,
                        "client_id": "identity-center",
                        "organization_id": "federated-keycloak",
                        "access_token_audience": "egress_gateway",
                        "claim_mapping": {"display_name": "employee.full_name"},
                    }
                ]
            }
        )
    )
    service = await IdentityFederationService.create(
        rt.handler,
        _settings(
            IDENTITY_FEDERATION_CONFIG_FILE=str(config),
            IDENTITY_FEDERATION_OIDC_CLIENT_SECRET=None,
        ),
    )
    rt.browser = service
    try:
        yield rt
    finally:
        await service.close()


def _browser_token(rt, **changes):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "egress_gateway",
        "sub": "subject-1",
        "azp": "identity-center",
        "iat": now,
        "exp": now + 300,
        "preferred_username": "employee-1",
        "employee": {"full_name": "Employee One"},
    }
    claims.update(changes)
    return jwt.encode(claims, rt.key, algorithm="RS256", headers={"kid": rt.kid})


@pytest.mark.asyncio
async def test_browser_login_preserves_existing_identity_without_backend_exchange(
    browser_runtime,
):
    rt = browser_runtime
    _, callback, _ = await _begin(rt)
    _, existing = await rt.service.complete_callback(
        "keycloak", callback, browser_binding=BROWSER
    )
    rt.calls.clear()
    token = _browser_token(rt)
    result = await rt.browser.login_with_access_token("keycloak", token)
    claims = jwt.decode(result["access_token"], options={"verify_signature": False})
    assert claims["sub"] == existing.user_id
    assert result["federation"]["access_token"] == token
    assert not any(path.endswith("/token") for path in rt.calls)
    mapped = await rt.handler.get("identity_user", {"user_id": existing.user_id})
    assert mapped.display_name == "Employee One"
    assert not mapped.is_admin
    assert (
        len(await rt.handler.list_records("federated_identity", {}, limit=10, offset=0))
        == 1
    )
    config = await rt.browser.browser_configuration("keycloak")
    assert config["client_id"] == "identity-center"
    assert config["flow"] == "browser_pkce"
    assert "secret" not in json.dumps(config)
    with pytest.raises(FederationError, match="server callback"):
        await rt.browser.begin_login("keycloak", "/auth")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://untrusted.example.test"},
        {"aud": "wrong"},
        {"azp": "another-client"},
        {"exp": 1},
        {"iat": int(time.time()) + 120},
        {"sub": ""},
        {"sub": 1},
        {"typ": "ID"},
    ],
)
async def test_browser_token_trust_failures_do_not_create_users(
    browser_runtime, changes
):
    rt = browser_runtime
    with pytest.raises(FederationError):
        await rt.browser.login_with_access_token(
            "keycloak", _browser_token(rt, **changes)
        )
    assert not await rt.handler.list_records(
        "federated_identity", {}, limit=10, offset=0
    )


@pytest.mark.asyncio
async def test_browser_requires_exp_and_rejects_forgery(browser_runtime):
    rt = browser_runtime
    raw = _browser_token(rt)
    claims = jwt.decode(raw, options={"verify_signature": False})
    del claims["exp"]
    for token in (
        jwt.encode(claims, rt.key, algorithm="RS256", headers={"kid": rt.kid}),
        jwt.encode(
            claims,
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
            algorithm="RS256",
            headers={"kid": rt.kid},
        ),
        jwt.encode(
            claims,
            "untrusted-hmac-secret-more-than-32-bytes",
            algorithm="HS256",
            headers={"kid": rt.kid},
        ),
    ):
        with pytest.raises(FederationError):
            await rt.browser.login_with_access_token("keycloak", token)


@pytest.mark.asyncio
async def test_browser_disabled_user_and_refresh_binding(browser_runtime):
    rt = browser_runtime
    token = _browser_token(rt)
    result = await rt.browser.login_with_access_token("keycloak", token)
    uid = result["federation"]["user_id"]
    handoff = await rt.browser.refresh_token_handoff("keycloak", token, uid)
    assert handoff["access_token"] == token
    with pytest.raises(FederationError, match="active platform user"):
        await rt.browser.refresh_token_handoff(
            "keycloak", _browser_token(rt, sub="someone-else"), uid
        )
    with pytest.raises(FederationError):
        await rt.browser.refresh_token_handoff("keycloak", token, "another-local-user")
    await rt.handler.update("identity_user", {"user_id": uid}, {"status": "disabled"})
    assert await rt.browser.login_with_access_token("keycloak", token) == "disabled"
    with pytest.raises(FederationError):
        await rt.browser.refresh_token_handoff("keycloak", token, uid)


@pytest.mark.asyncio
async def test_browser_http_contract_and_credential_separation(browser_runtime):
    from fastapi import FastAPI
    from identity_center.routers.federation_routers import federation_router

    rt = browser_runtime
    app = FastAPI()
    app.state.federation_service = rt.browser
    app.include_router(federation_router, prefix="/v1/auth/federation")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://app.example.test"
    ) as http:
        listed = (await http.get("/v1/auth/federation/connections")).json()
        assert listed["connections"][0]["flow"] == "browser_pkce"
        for token in ("", "Bearer forged"):
            response = await http.post(
                "/v1/auth/federation/keycloak/token-login",
                headers={"Authorization": token},
            )
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
        raw = _browser_token(rt)
        response = await http.post(
            "/v1/auth/federation/keycloak/token-login",
            headers={"Authorization": "Bearer " + raw},
        )
        assert response.status_code == 200
        bundle = response.json()
        assert (
            bundle["access_token"] != raw
            and bundle["federation"]["access_token"] == raw
        )
        assert "refresh_token" not in bundle["federation"]
        response = await http.post(
            "/v1/auth/federation/keycloak/token-handoff",
            headers={
                "Authorization": "Bearer " + bundle["access_token"],
                "X-User-Token": "Bearer " + raw,
            },
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == bundle["federation"]["user_id"]
        assert (
            await http.get("/v1/auth/federation/unknown/configuration")
        ).status_code == 404


def test_multiple_connections_and_invalid_configuration(tmp_path):
    from identity_center.core.federation.connections import load_oidc_connections
    from pydantic import ValidationError

    path = tmp_path / "connections.json"
    customer = {
        "connection_id": "customer-one",
        "issuer": ISSUER,
        "client_id": "web-one",
        "organization_id": "customer-one-org",
        "access_token_audience": "tools",
    }
    path.write_text(
        json.dumps(
            {
                "connections": [
                    customer,
                    dict(customer, connection_id="customer-two", client_id="web-two"),
                ]
            }
        )
    )
    settings = _settings(IDENTITY_FEDERATION_CONFIG_FILE=str(path))
    loaded = load_oidc_connections(settings)
    assert [c.client_id for c in loaded] == ["web-one", "web-two"]
    assert all(c.flow == "browser_pkce" and c.client_secret is None for c in loaded)
    for invalid in (
        dict(customer, client_secret="not-for-browser"),
        dict(customer, flow="unknown"),
        dict(customer, claim_mapping={"external_subject": "email"}),
    ):
        path.write_text(json.dumps({"connections": [invalid]}))
        with pytest.raises(ValidationError):
            load_oidc_connections(settings)
    path.write_text(json.dumps({"connections": [customer, customer]}))
    with pytest.raises(ValueError, match="unique"):
        load_oidc_connections(settings)


@pytest.mark.asyncio
async def test_environment_browser_flow_without_login_secret(runtime):
    rt = runtime
    settings = _settings(
        IDENTITY_FEDERATION_OIDC_FLOW="browser_pkce",
        IDENTITY_FEDERATION_OIDC_CLIENT_SECRET=None,
        IDENTITY_FEDERATION_OIDC_REDIRECT_URI="",
    )
    service = await IdentityFederationService.create(rt.handler, settings)
    options = federation_connections.load_oidc_connections(settings)[0]
    try:
        config = await service.browser_configuration("keycloak")
        assert config["flow"] == "browser_pkce"
        assert config["authority"] == ISSUER
        assert config["client_id"] == "identity-center"
        assert "client_secret" not in config and "redirect_uri" not in config
        result = await service.login_with_access_token("keycloak", _browser_token(rt))
        assert result["federation"]["connection_id"] == "keycloak"
        assert options.require_exp is True
        assert not any(path.endswith("/token") for path in rt.calls)
    finally:
        await service.close()


def test_environment_legacy_flow_keeps_secret_requirement():
    from identity_center.core.federation.connections import load_oidc_connections
    from pydantic import ValidationError

    options = load_oidc_connections(_settings())[0]
    assert options.flow == "server_code" and options.require_exp is False
    with pytest.raises(ValidationError, match="server_code requires"):
        load_oidc_connections(_settings(IDENTITY_FEDERATION_OIDC_CLIENT_SECRET=None))


def test_connection_file_precedes_single_customer_environment(tmp_path):
    from identity_center.core.federation.connections import load_oidc_connections

    path = tmp_path / "empty-connections.json"
    path.write_text('{"connections": []}')
    settings = _settings(
        IDENTITY_FEDERATION_CONFIG_FILE=str(path),
        IDENTITY_FEDERATION_OIDC_FLOW="browser_pkce",
        IDENTITY_SSO_LOGIN_CLIENT_ID="unused-partial-environment",
    )
    # A configured file owns the connection list, including explicit disablement.
    assert load_oidc_connections(settings) == []


def test_environment_rejects_unknown_flow():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(IDENTITY_FEDERATION_OIDC_FLOW="unsupported")


@pytest.mark.asyncio
async def test_sso_settings_select_browser_login_without_flow(runtime):
    rt = runtime
    settings = _settings(
        IDENTITY_SSO_SERVER_URL=ISSUER,
        IDENTITY_SSO_LOGIN_CLIENT_ID="identity-center",
        IDENTITY_SSO_TOKEN_AUDIENCE="egress_gateway",
    )
    # Old backend credentials may still be present during an upgrade. The new
    # browser-login settings never use or expose them.
    service = await IdentityFederationService.create(rt.handler, settings)
    options = federation_connections.load_oidc_connections(settings)[0]
    try:
        assert service.flow("keycloak") == "browser_pkce"
        assert options.client_secret is None
        assert options.redirect_uri == ""
        public = await service.browser_configuration("keycloak")
        assert public["authority"] == ISSUER
        assert public["client_id"] == "identity-center"
        result = await service.login_with_access_token("keycloak", _browser_token(rt))
        assert result["federation"]["access_token"]
        assert not any(path.endswith("/token") for path in rt.calls)
    finally:
        await service.close()


def test_sso_address_enables_login_with_explicit_audience():
    from identity_center.core.federation.connections import load_oidc_connections

    settings = _settings(
        IDENTITY_SSO_SERVER_URL=ISSUER,
        IDENTITY_SSO_LOGIN_CLIENT_ID="web-login",
        IDENTITY_SSO_TOKEN_AUDIENCE="company-api",
        IDENTITY_FEDERATION_OIDC_ISSUER="",
    )
    assert settings.federation_oidc_enabled
    options = load_oidc_connections(settings)[0]
    assert options.client_id == "web-login"
    assert options.access_token_audience == "company-api"
    assert options.require_exp
    assert not _settings(IDENTITY_FEDERATION_OIDC_ISSUER="").federation_oidc_enabled


@pytest.mark.parametrize("audience", [None, "", "   "])
def test_sso_login_requires_explicit_audience(audience):
    from identity_center.core.federation.connections import load_oidc_connections

    values = {
        "IDENTITY_SSO_SERVER_URL": ISSUER,
        "IDENTITY_SSO_LOGIN_CLIENT_ID": "web-login",
    }
    if audience is not None:
        values["IDENTITY_SSO_TOKEN_AUDIENCE"] = audience
    settings = _settings(**values)
    assert settings.sso_token_audience == (audience or "")
    with pytest.raises(ValueError, match="IDENTITY_SSO_TOKEN_AUDIENCE"):
        load_oidc_connections(settings)


def test_sso_login_requires_its_own_client_id():
    from identity_center.core.federation.connections import load_oidc_connections

    with pytest.raises(ValueError, match="IDENTITY_SSO_LOGIN_CLIENT_ID"):
        load_oidc_connections(
            _settings(
                IDENTITY_SSO_SERVER_URL=ISSUER,
                IDENTITY_SSO_TOKEN_AUDIENCE="company-api",
            )
        )


@pytest.mark.parametrize("mask", range(1, 7))
def test_sso_partial_settings_name_missing_environment_variables(mask):
    values = {
        "IDENTITY_SSO_SERVER_URL": ISSUER,
        "IDENTITY_SSO_LOGIN_CLIENT_ID": "web-login",
        "IDENTITY_SSO_TOKEN_AUDIENCE": "company-api",
    }
    partial_values = {
        name: value if mask & (1 << index) else "   "
        for index, (name, value) in enumerate(values.items())
    }
    settings = _settings(IDENTITY_FEDERATION_OIDC_ISSUER="", **partial_values)
    assert settings.federation_oidc_enabled
    with pytest.raises(ValueError, match="Enterprise SSO requires all three") as caught:
        federation_connections.load_oidc_connections(settings)
    for name, value in partial_values.items():
        assert (name in str(caught.value)) is (not value.strip())


def test_sso_blank_settings_keep_local_login_configuration():
    settings = _settings(
        IDENTITY_SSO_SERVER_URL="   ",
        IDENTITY_SSO_LOGIN_CLIENT_ID="",
        IDENTITY_SSO_TOKEN_AUDIENCE="   ",
        IDENTITY_FEDERATION_OIDC_ISSUER="",
    )
    assert not settings.federation_oidc_enabled
    assert federation_connections.load_oidc_connections(settings) == []
