"""Browser routes joining enterprise federation to local OAuth2/JWT tokens."""

from __future__ import annotations

import html
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from openjiuwen_runtime.service import (
    FederationError,
    UnknownFederationConnection,
)

from identity_center.core.federation import IdentityFederationService
from identity_center.infrastructure.logger import get_logger
from identity_center.routers.deps import get_current_claims
from identity_center.schemas.federation_schemas import (
    FederatedTokenResponse,
    FederationAccessToken,
    FederationBrowserConfiguration,
    FederationCodeExchangeBody,
    FederationConnectionOut,
    FederationConnectionsOut,
)

federation_router = APIRouter()
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
_log = get_logger(__name__)


def get_federation_service(request: Request) -> IdentityFederationService:
    service = getattr(request.app.state, "federation_service", None)
    if not isinstance(service, IdentityFederationService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="federation service is unavailable",
        )
    return service


_Service = Annotated[IdentityFederationService, Depends(get_federation_service)]
_Claims = Annotated[dict, Depends(get_current_claims)]


@federation_router.get("/connections", response_model=FederationConnectionsOut)
async def list_connections(service: _Service):
    """List enabled enterprise login choices without exposing issuer details."""
    return FederationConnectionsOut(
        connections=[
            FederationConnectionOut(
                connection_id=connection.connection_id,
                name=connection.organization_name,
                flow=service.flow(connection.connection_id),
            )
            for connection in service.connections
        ]
    )


@federation_router.get(
    "/{connection_id}/configuration", response_model=FederationBrowserConfiguration
)
async def browser_configuration(
    connection_id: str, response: Response, service: _Service
):
    response.headers.update(_PRIVATE_HEADERS)
    try:
        return await service.browser_configuration(connection_id)
    except UnknownFederationConnection as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@federation_router.post(
    "/{connection_id}/token-login",
    response_model=FederatedTokenResponse,
    response_model_exclude_none=True,
)
async def browser_token_login(
    connection_id: str, request: Request, response: Response, service: _Service
):
    response.headers.update(_PRIVATE_HEADERS)
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip() or len(token) > 32768:
        raise HTTPException(
            status_code=401,
            detail="invalid_external_access_token",
            headers=_PRIVATE_HEADERS,
        )
    try:
        result = await service.login_with_access_token(connection_id, token.strip())
    except UnknownFederationConnection as exc:
        raise HTTPException(
            status_code=404, detail=str(exc), headers=_PRIVATE_HEADERS
        ) from exc
    except FederationError as exc:
        raise HTTPException(
            status_code=401,
            detail="invalid_external_access_token",
            headers=_PRIVATE_HEADERS,
        ) from exc
    if isinstance(result, str):
        raise HTTPException(
            status_code=403 if result == "disabled" else 401,
            detail=result,
            headers=_PRIVATE_HEADERS,
        )
    return result


@federation_router.post(
    "/{connection_id}/token-handoff", response_model=FederationAccessToken
)
async def refresh_handoff(
    connection_id: str,
    request: Request,
    response: Response,
    service: _Service,
    claims: _Claims,
):
    response.headers.update(_PRIVATE_HEADERS)
    scheme, _, token = request.headers.get("X-User-Token", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip() or len(token) > 32768:
        raise HTTPException(
            status_code=401,
            detail="invalid_external_access_token",
            headers=_PRIVATE_HEADERS,
        )
    try:
        return await service.refresh_token_handoff(
            connection_id, token.strip(), str(claims["sub"])
        )
    except UnknownFederationConnection as exc:
        raise HTTPException(
            status_code=404, detail=str(exc), headers=_PRIVATE_HEADERS
        ) from exc
    except FederationError as exc:
        raise HTTPException(
            status_code=403,
            detail="invalid_external_token_binding",
            headers=_PRIVATE_HEADERS,
        ) from exc


@federation_router.get("/{connection_id}/login")
async def begin_login(
    connection_id: str,
    request: Request,
    service: _Service,
    return_to: str = Query(default="/auth"),
):
    try:
        bound = service.requires_browser_binding(connection_id)
        binding = request.cookies.get(service.browser_cookie_name, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", binding):
            binding = secrets.token_urlsafe(32)
        login_url = await service.begin_login(
            connection_id, return_to, browser_binding=binding if bound else None
        )
    except UnknownFederationConnection as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = RedirectResponse(
        login_url, status_code=status.HTTP_303_SEE_OTHER, headers=_PRIVATE_HEADERS
    )
    if bound:
        response.set_cookie(
            service.browser_cookie_name,
            binding,
            httponly=True,
            samesite="lax",
            secure=service.secure_browser_cookie,
            max_age=service.browser_cookie_ttl,
            path=service.browser_cookie_path,
        )
    return response


@federation_router.get("/{connection_id}/callback")
async def complete_oidc_login(connection_id: str, request: Request, service: _Service):
    """OIDC query callback: only a server-validated identity can create a local code."""
    try:
        if not service.requires_browser_binding(connection_id):
            raise HTTPException(
                status_code=405, detail="this provider requires a POST callback"
            )
        parameters = dict(request.query_params)
        for key in ("code", "state", "iss", "error"):
            if len(request.query_params.getlist(key)) > 1:
                raise FederationError("duplicate OIDC callback parameter")
        if (
            len(parameters.get("state", "")) > 128
            or len(parameters.get("code", "")) > 4096
        ):
            raise FederationError("invalid OIDC callback parameter")
        redirect_url, _ = await service.complete_callback(
            connection_id,
            parameters,
            browser_binding=request.cookies.get(service.browser_cookie_name),
        )
    except UnknownFederationConnection as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationError as exc:
        # Never echo upstream error descriptions, authorization codes or state.
        _log.warning(
            "federation.oidc_login_failed", connection_id=connection_id, reason=str(exc)
        )
        redirect_url = "/auth?federation_error=authentication_failed"
    return RedirectResponse(
        redirect_url, status_code=status.HTTP_303_SEE_OTHER, headers=_PRIVATE_HEADERS
    )


@federation_router.get(
    "/demo/idp/login",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def demo_login(
    connection_id: str,
    authorization_request_id: str,
    service: _Service,
):
    """Render the explicitly labelled development-only enterprise login form."""
    if not service.demo_enabled or connection_id != "enterprise-demo":
        raise HTTPException(status_code=404, detail="demo federation is disabled")
    try:
        await service.require_pending_request(
            connection_id,
            authorization_request_id,
        )
    except FederationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    safe_connection = html.escape(connection_id, quote=True)
    safe_request = html.escape(authorization_request_id, quote=True)
    safe_admin_group = html.escape(service.demo_admin_group)
    action = html.escape(
        service.public_path(f"/v1/auth/federation/{connection_id}/callback"),
        quote=True,
    )
    return HTMLResponse(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Enterprise Demo IdP</title>
<style>
body{{font-family:system-ui;max-width:460px;margin:60px auto;color:#222}}
.card{{border:1px solid #ddd;border-radius:12px;padding:24px}}
label,input,button{{display:block;width:100%;box-sizing:border-box}}
input{{padding:10px;margin:6px 0 14px}}button{{padding:11px}}
.warning{{background:#fff3cd;padding:10px;border-radius:6px;font-size:14px}}
</style></head><body><div class="card">
<h2>Enterprise Demo IdP</h2>
<p class="warning">Local simulation only. No SAML XML is accepted or verified.</p>
<form method="post" action="{action}">
<input type="hidden" name="authorization_request_id" value="{safe_request}">
<input type="hidden" name="connection_id" value="{safe_connection}">
<label>Employee ID<input name="employee_id" value="employee-10086" required></label>
<label>Display name<input name="display_name" value="Enterprise Alice" required></label>
<label>Email<input name="email" value="alice@enterprise.example"></label>
<label>Groups (comma-separated)<input name="groups" value="employees"></label>
<p>Use <code>{safe_admin_group}</code> to simulate a verified enterprise admin group.</p>
<button type="submit">Enterprise sign in</button></form>
</div></body></html>"""
    )


@federation_router.post("/{connection_id}/callback")
async def complete_login(connection_id: str, request: Request, service: _Service):
    form_data = await request.form()
    parameters = {key: str(value) for key, value in form_data.items()}
    try:
        if service.requires_browser_binding(connection_id):
            raise HTTPException(status_code=405, detail="OIDC requires a GET callback")
        redirect_url, _ = await service.complete_callback(
            connection_id,
            parameters,
        )
    except UnknownFederationConnection as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@federation_router.post(
    "/exchange", response_model=FederatedTokenResponse, response_model_exclude_none=True
)
async def exchange_code(
    body: FederationCodeExchangeBody,
    request: Request,
    response: Response,
    service: _Service,
):
    result = await service.exchange_code(
        body.code, browser_binding=request.cookies.get(service.browser_cookie_name)
    )
    if isinstance(result, str):
        status_code = (
            status.HTTP_403_FORBIDDEN
            if result == "disabled"
            else status.HTTP_401_UNAUTHORIZED
        )
        raise HTTPException(status_code=status_code, detail=result)
    response.headers.update(_PRIVATE_HEADERS)
    return FederatedTokenResponse(
        access_token=result["access_token"],
        token_type=result["token_type"],
        expires_in=result["expires_in"],
        refresh_token=result["refresh_token"],
        federation=result.get("federation"),
    )


__all__ = ["federation_router", "get_federation_service"]
