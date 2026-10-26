"""JWKS refresh throttling, key rotation and recovery through the public verifier."""

from __future__ import annotations

import asyncio
import json
import time
from functools import partial
from types import SimpleNamespace
from urllib.parse import urljoin

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from identity_center.core.federation.oidc import verifier as verifier_module
from identity_center.core.federation.oidc.config import OidcConnectionConfig
from identity_center.core.federation.oidc.verifier import OidcTokenVerifier
from openjiuwen_runtime.service import FederationError

ISSUER = "https://sso.example.test/realms/cache-tests"


@pytest_asyncio.fixture(name="cache_context")
async def cache_context_fixture(monkeypatch):
    context = SimpleNamespace(
        key=rsa.generate_private_key(public_exponent=65537, key_size=2048),
        kid="known-key",
        clock=1000.0,
        fetches=0,
        status=200,
        jwks=None,
    )
    monkeypatch.setattr(
        verifier_module, "time", SimpleNamespace(monotonic=lambda: context.clock)
    )

    async def upstream(request):
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": urljoin(f"{ISSUER}/", "auth"),
                    "token_endpoint": urljoin(f"{ISSUER}/", "token"),
                    "jwks_uri": urljoin(f"{ISSUER}/", "certs"),
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        assert request.url.path.endswith("/certs")
        context.fetches += 1
        if context.status != 200:
            return httpx.Response(context.status)
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(context.key.public_key()))
        jwks = context.jwks
        if jwks is None:
            jwks = {"keys": [dict(key, kid=context.kid, use="sig", alg="RS256")]}
        return httpx.Response(200, json=jwks)

    client_factory = partial(httpx.AsyncClient, transport=httpx.MockTransport(upstream))
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    context.options = OidcConnectionConfig(
        connection_id="cache-tests",
        issuer=ISSUER,
        client_id="web-login",
        organization_id="test-organization",
        access_token_audience="test-tools",
    )
    context.verifier = OidcTokenVerifier(context.options)
    try:
        yield context
    finally:
        await context.verifier.close()


async def _verify(context, kid=None):
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "subject-1",
            "aud": "test-tools",
            "azp": "web-login",
            "iat": now,
            "exp": now + 300,
        },
        context.key,
        algorithm="RS256",
        headers={"kid": kid or context.kid},
    )
    return await context.verifier.validate_access_token(
        {"access_token": token, "token_type": "Bearer"}, audience="test-tools"
    )


@pytest.mark.asyncio
async def test_unknown_kid_burst_shares_one_refresh_and_keeps_known_keys(cache_context):
    context = cache_context
    await _verify(context)
    results = await asyncio.gather(
        *(_verify(context, kid) for kid in ("missing", "missing", "new-1", "new-2")),
        return_exceptions=True,
    )
    assert all(isinstance(result, FederationError) for result in results)
    assert context.fetches == 2
    assert (await _verify(context))["sub"] == "subject-1"
    assert context.fetches == 2


@pytest.mark.asyncio
async def test_rotation_after_normal_cache_load_does_not_wait_for_cooldown(cache_context):
    context = cache_context
    await _verify(context)
    context.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    context.kid = "rotated-key"
    assert (await _verify(context))["sub"] == "subject-1"
    assert context.fetches == 2


@pytest.mark.asyncio
async def test_miss_cooldown_expires_and_discovers_rotated_key(cache_context):
    context = cache_context
    await _verify(context)
    with pytest.raises(FederationError, match="access token validation failed"):
        await _verify(context, "missing")
    context.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    context.kid = "rotated-key"
    context.clock = 1059
    with pytest.raises(FederationError, match="access token validation failed"):
        await _verify(context)
    assert context.fetches == 2
    context.clock = 1060
    assert (await _verify(context))["sub"] == "subject-1"
    assert context.fetches == 3


@pytest.mark.asyncio
async def test_expired_cache_refreshes_known_key(cache_context):
    context = cache_context
    await _verify(context)
    context.clock += 301
    assert (await _verify(context))["sub"] == "subject-1"
    assert context.fetches == 2


@pytest.mark.asyncio
async def test_failed_initial_fetch_is_throttled_and_recovers(cache_context):
    context = cache_context
    context.status = 503
    for _ in range(3):
        with pytest.raises(FederationError):
            await _verify(context)
    assert context.fetches == 1
    context.status = 200
    context.clock += 60
    assert (await _verify(context))["sub"] == "subject-1"
    assert context.fetches == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("jwks", [{"keys": "invalid"}, {"keys": ["invalid"]}])
async def test_invalid_refresh_does_not_replace_cached_keys(cache_context, jwks):
    context = cache_context
    await _verify(context)
    context.jwks = jwks
    with pytest.raises(FederationError, match="access token validation failed"):
        await _verify(context, "missing")
    with pytest.raises(FederationError, match="access token validation failed"):
        await _verify(context, "another-missing")
    assert (await _verify(context))["sub"] == "subject-1"
    assert context.fetches == 2


@pytest.mark.asyncio
async def test_refresh_cooldown_is_isolated_between_connections(cache_context):
    context = cache_context
    await _verify(context)
    with pytest.raises(FederationError):
        await _verify(context, "missing")
    other = OidcTokenVerifier(
        context.options.model_copy(update={"connection_id": "other-connection"})
    )
    try:
        other_context = SimpleNamespace(**vars(context))
        other_context.verifier = other
        assert (await _verify(other_context))["sub"] == "subject-1"
        assert context.fetches == 3
    finally:
        await other.close()
