# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Small OAuth2 Authorization Code server used only by the runnable example."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import secrets
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .domain import FederationConnection, LocalPrincipal


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@dataclass(frozen=True)
class _AuthorizationRequest:
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str | None
    code_challenge_method: str | None
    expires_at: float


@dataclass(frozen=True)
class _AuthorizationCode:
    principal: LocalPrincipal
    client_id: str
    redirect_uri: str
    code_challenge: str | None
    code_challenge_method: str | None
    expires_at: float


@dataclass(frozen=True)
class _StoredAccessToken:
    principal: LocalPrincipal
    expires_at: float


class OAuth2FlowError(Exception):
    """OAuth2-compatible error raised by the example authorization server."""

    def __init__(self, error: str, description: str, status_code: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


class ExampleOAuth2AuthorizationServer:
    """Issue one-time authorization codes and local bearer access tokens."""

    def __init__(
        self,
        *,
        client_id: str = "swagger-docs",
        authorization_ttl: int = 300,
        access_token_ttl: int = 3600,
    ) -> None:
        self.client_id = client_id
        self._authorization_ttl = authorization_ttl
        self._access_token_ttl = access_token_ttl
        self._requests: dict[str, _AuthorizationRequest] = {}
        self._codes: dict[str, _AuthorizationCode] = {}
        self._tokens: dict[str, _StoredAccessToken] = {}
        self._lock = asyncio.Lock()

    def mount(
        self,
        fastapi: FastAPI,
        connections: Iterable[FederationConnection],
    ) -> None:
        """Mount the example authorization and token endpoints."""
        enterprise_connections = tuple(connections)

        @fastapi.get(
            "/oauth/authorize",
            response_class=HTMLResponse,
            tags=["authentication"],
        )
        async def authorize(request: Request):
            try:
                authorization_request_id = await self.begin_authorization(
                    response_type=request.query_params.get("response_type", ""),
                    client_id=request.query_params.get("client_id", ""),
                    redirect_uri=request.query_params.get("redirect_uri", ""),
                    state=request.query_params.get("state", ""),
                    code_challenge=request.query_params.get("code_challenge"),
                    code_challenge_method=request.query_params.get(
                        "code_challenge_method"
                    ),
                )
            except OAuth2FlowError as exc:
                return _oauth_error(exc)
            return HTMLResponse(
                _authorization_page(
                    authorization_request_id,
                    enterprise_connections,
                )
            )

        @fastapi.post("/auth/local/login", tags=["authentication"])
        async def local_login(request: Request):
            form = await request.form()
            username = str(form.get("username") or "")
            password = str(form.get("password") or "")
            request_id = str(form.get("authorization_request_id") or "")
            if username != "demo" or password != "demo":
                return HTMLResponse(
                    "<h2>Incorrect username or password</h2>",
                    status_code=401,
                )
            principal = LocalPrincipal(
                user_id="local-demo-user",
                organization_id="local-demo-organization",
                display_name="demo",
                roles=("developer",),
                auth_source="local",
            )
            try:
                redirect_url = await self.complete_authorization(
                    request_id,
                    principal,
                )
            except OAuth2FlowError as exc:
                return _oauth_error(exc)
            return RedirectResponse(redirect_url, status_code=303)

        @fastapi.post(
            "/oauth/token", response_model=AccessToken, tags=["authentication"]
        )
        async def token(request: Request):
            form = await request.form()
            try:
                access_token = await self.exchange_code(
                    grant_type=str(form.get("grant_type") or ""),
                    code=str(form.get("code") or ""),
                    client_id=str(form.get("client_id") or ""),
                    redirect_uri=str(form.get("redirect_uri") or ""),
                    code_verifier=str(form.get("code_verifier") or "") or None,
                )
            except OAuth2FlowError as exc:
                return _oauth_error(exc)
            return access_token

    async def begin_authorization(
        self,
        *,
        response_type: str,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str | None,
        code_challenge_method: str | None,
    ) -> str:
        if response_type != "code":
            raise OAuth2FlowError(
                "unsupported_response_type",
                "the example only supports response_type=code",
            )
        if client_id != self.client_id:
            raise OAuth2FlowError("invalid_client", "unknown OAuth2 client")
        _validate_docs_redirect_uri(redirect_uri)
        if code_challenge and code_challenge_method != "S256":
            raise OAuth2FlowError(
                "invalid_request",
                "the example only supports PKCE S256",
            )

        request_id = secrets.token_urlsafe(24)
        async with self._lock:
            self._remove_expired_locked()
            self._requests[request_id] = _AuthorizationRequest(
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                expires_at=time.time() + self._authorization_ttl,
            )
        return request_id

    async def require_authorization_request(self, request_id: str) -> None:
        async with self._lock:
            self._remove_expired_locked()
            if request_id not in self._requests:
                raise OAuth2FlowError(
                    "invalid_request",
                    "authorization request is missing or expired",
                )

    async def complete_authorization(
        self,
        request_id: str,
        principal: LocalPrincipal,
    ) -> str:
        async with self._lock:
            self._remove_expired_locked()
            authorization = self._requests.pop(request_id, None)
            if authorization is None:
                raise OAuth2FlowError(
                    "invalid_request",
                    "authorization request is missing or expired",
                )
            code = secrets.token_urlsafe(32)
            self._codes[code] = _AuthorizationCode(
                principal=principal,
                client_id=authorization.client_id,
                redirect_uri=authorization.redirect_uri,
                code_challenge=authorization.code_challenge,
                code_challenge_method=authorization.code_challenge_method,
                expires_at=time.time() + self._authorization_ttl,
            )
        return _append_query(
            authorization.redirect_uri,
            {"code": code, "state": authorization.state},
        )

    async def exchange_code(
        self,
        *,
        grant_type: str,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str | None,
    ) -> AccessToken:
        if grant_type != "authorization_code":
            raise OAuth2FlowError(
                "unsupported_grant_type",
                "the example only supports authorization_code",
            )
        async with self._lock:
            self._remove_expired_locked()
            authorization = self._codes.get(code)
            if authorization is None:
                raise OAuth2FlowError("invalid_grant", "code is invalid or expired")
            if client_id != authorization.client_id:
                raise OAuth2FlowError("invalid_client", "client_id does not match code")
            if redirect_uri != authorization.redirect_uri:
                raise OAuth2FlowError(
                    "invalid_grant",
                    "redirect_uri does not match authorization request",
                )
            _verify_pkce(authorization, code_verifier)
            del self._codes[code]

            token = secrets.token_urlsafe(40)
            self._tokens[token] = _StoredAccessToken(
                principal=authorization.principal,
                expires_at=time.time() + self._access_token_ttl,
            )
        return AccessToken(
            access_token=token,
            expires_in=self._access_token_ttl,
        )

    async def validate_access_token(self, token: str):
        """Return the principal payload consumed by OAuth2AccessControl."""
        async with self._lock:
            self._remove_expired_locked()
            stored = self._tokens.get(token)
            if stored is None:
                return None
            principal = stored.principal
            payload = principal.model_dump(mode="json")
            payload["username"] = principal.display_name
            return payload

    def _remove_expired_locked(self) -> None:
        now = time.time()
        self._requests = {
            key: value
            for key, value in self._requests.items()
            if value.expires_at > now
        }
        self._codes = {
            key: value for key, value in self._codes.items() if value.expires_at > now
        }
        self._tokens = {
            key: value for key, value in self._tokens.items() if value.expires_at > now
        }


def _authorization_page(
    authorization_request_id: str,
    connections: tuple[FederationConnection, ...],
) -> str:
    request_id = html.escape(authorization_request_id, quote=True)
    enterprise_links = "".join(
        (
            '<a class="sso" href="/auth/federation/'
            f"{html.escape(connection.connection_id, quote=True)}"
            f'/login?authorization_request_id={request_id}">'
            f"Sign in with {html.escape(connection.organization_name)}</a>"
        )
        for connection in connections
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>OAuth2 Sign In</title>
<style>
body{{font-family:system-ui;max-width:460px;margin:60px auto;color:#222}}
.card{{border:1px solid #ddd;border-radius:12px;padding:24px}}
label,input,button,.sso{{display:block;width:100%;box-sizing:border-box}}
input{{padding:10px;margin:6px 0 14px}}button,.sso{{padding:11px;margin-top:12px}}
.sso{{text-align:center;background:#1769aa;color:white;text-decoration:none;border-radius:6px}}
.hint{{color:#666;font-size:14px}}
</style></head><body><div class="card">
<h2>OpenJiuwen OAuth2</h2>
<p class="hint">Local demo account: demo / demo</p>
<form method="post" action="/auth/local/login">
<input type="hidden" name="authorization_request_id" value="{request_id}">
<label>Username<input name="username" value="demo" required></label>
<label>Password<input name="password" type="password" value="demo" required></label>
<button type="submit">Local sign in</button></form>
<hr>{enterprise_links}
</div></body></html>"""


def _validate_docs_redirect_uri(redirect_uri: str) -> None:
    parsed = urlsplit(redirect_uri)
    valid_origin = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    valid_callback = parsed.path == "/docs/oauth2-redirect" and not parsed.fragment
    if not valid_origin or not valid_callback:
        raise OAuth2FlowError(
            "invalid_request",
            "redirect_uri must target /docs/oauth2-redirect",
        )


def _append_query(url: str, values: dict[str, str]) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(values.items())
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _verify_pkce(
    authorization: _AuthorizationCode,
    code_verifier: str | None,
) -> None:
    if authorization.code_challenge is None:
        return
    if authorization.code_challenge_method != "S256" or not code_verifier:
        raise OAuth2FlowError("invalid_grant", "a PKCE code_verifier is required")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if not secrets.compare_digest(challenge, authorization.code_challenge):
        raise OAuth2FlowError("invalid_grant", "PKCE verification failed")


def _oauth_error(exc: OAuth2FlowError) -> JSONResponse:
    return JSONResponse(
        {"error": exc.error, "error_description": exc.description},
        status_code=exc.status_code,
    )
