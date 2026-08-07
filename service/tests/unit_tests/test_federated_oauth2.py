# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Unit tests for the modular federation-to-OAuth2 flow."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from examples.federated_auth import (
    DemoFederationProvider,
    ExampleOAuth2AuthorizationServer,
    FederationConnection,
    InMemoryFederatedIdentityStore,
)
from examples.federated_auth.oauth2_server import OAuth2FlowError

REDIRECT_URI = "http://testserver/docs/oauth2-redirect"
VERIFIER = "unit-test-verifier-abcdefghijklmnopqrstuvwxyz-0123456789"


def _challenge() -> str:
    digest = hashlib.sha256(VERIFIER.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@pytest.mark.unit
async def test_federated_principal_uses_same_oauth2_authorization_code_flow():
    oauth2 = ExampleOAuth2AuthorizationServer()
    store = InMemoryFederatedIdentityStore()
    provider = DemoFederationProvider()
    connection = FederationConnection(
        connection_id="enterprise-demo",
        issuer="https://idp.enterprise-demo.example",
        organization_id="virtual-org-demo",
        organization_name="Enterprise Demo",
    )
    request_id = await oauth2.begin_authorization(
        response_type="code",
        client_id="swagger-docs",
        redirect_uri=REDIRECT_URI,
        state="unit-state",
        code_challenge=_challenge(),
        code_challenge_method="S256",
    )
    result = await provider.consume_callback(
        connection,
        {
            "authorization_request_id": request_id,
            "employee_id": "employee-10086",
            "display_name": "Enterprise Alice",
        },
    )
    principal = await store.resolve_or_create(connection, result.identity)
    redirect_url = await oauth2.complete_authorization(request_id, principal)
    query = parse_qs(urlsplit(redirect_url).query)

    token = await oauth2.exchange_code(
        grant_type="authorization_code",
        code=query["code"][0],
        client_id="swagger-docs",
        redirect_uri=REDIRECT_URI,
        code_verifier=VERIFIER,
    )
    payload = await oauth2.validate_access_token(token.access_token)

    assert query["state"] == ["unit-state"]
    assert payload["user_id"] == principal.user_id
    assert payload["organization_id"] == "virtual-org-demo"
    assert payload["auth_source"] == "saml"

    with pytest.raises(OAuth2FlowError, match="invalid or expired"):
        await oauth2.exchange_code(
            grant_type="authorization_code",
            code=query["code"][0],
            client_id="swagger-docs",
            redirect_uri=REDIRECT_URI,
            code_verifier=VERIFIER,
        )


@pytest.mark.unit
async def test_oauth2_rejects_non_docs_redirect_uri():
    oauth2 = ExampleOAuth2AuthorizationServer()
    with pytest.raises(OAuth2FlowError, match="docs/oauth2-redirect"):
        await oauth2.begin_authorization(
            response_type="code",
            client_id="swagger-docs",
            redirect_uri="https://attacker.example/callback",
            state="unit-state",
            code_challenge=None,
            code_challenge_method=None,
        )
