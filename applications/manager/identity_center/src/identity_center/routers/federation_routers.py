"""Browser routes joining enterprise federation to local OAuth2/JWT tokens."""

from __future__ import annotations

import html
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from openjiuwen_runtime.service import (
    FederationError,
    UnknownFederationConnection,
)

from identity_center.core.federation import IdentityFederationService
from identity_center.schemas.auth_schemas import TokenResponse
from identity_center.schemas.federation_schemas import (
    FederationCodeExchangeBody,
    FederationConnectionOut,
    FederationConnectionsOut,
)

federation_router = APIRouter()


def get_federation_service(request: Request) -> IdentityFederationService:
    service = getattr(request.app.state, "federation_service", None)
    if not isinstance(service, IdentityFederationService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="federation service is unavailable",
        )
    return service


_Service = Annotated[IdentityFederationService, Depends(get_federation_service)]


@federation_router.get("/connections", response_model=FederationConnectionsOut)
async def list_connections(service: _Service):
    """List enabled enterprise login choices without exposing issuer details."""
    return FederationConnectionsOut(
        connections=[
            FederationConnectionOut(
                connection_id=connection.connection_id,
                name=connection.organization_name,
            )
            for connection in service.connections
        ]
    )


@federation_router.get("/{connection_id}/login")
async def begin_login(
    connection_id: str,
    service: _Service,
    return_to: str = Query(default="/auth"),
):
    try:
        login_url = await service.begin_login(connection_id, return_to)
    except UnknownFederationConnection as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(login_url, status_code=status.HTTP_303_SEE_OTHER)


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
        redirect_url, _ = await service.complete_callback(
            connection_id,
            parameters,
        )
    except UnknownFederationConnection as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@federation_router.post("/exchange", response_model=TokenResponse)
async def exchange_code(body: FederationCodeExchangeBody, service: _Service):
    result = await service.exchange_code(body.code)
    if isinstance(result, str):
        status_code = (
            status.HTTP_403_FORBIDDEN
            if result == "disabled"
            else status.HTTP_401_UNAUTHORIZED
        )
        raise HTTPException(status_code=status_code, detail=result)
    return TokenResponse(
        access_token=result["access_token"],
        token_type=result["token_type"],
        expires_in=result["expires_in"],
        refresh_token=result["refresh_token"],
    )


__all__ = ["federation_router", "get_federation_service"]
