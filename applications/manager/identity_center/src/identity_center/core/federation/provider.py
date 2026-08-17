"""Development-only enterprise identity provider adapter."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode

from openjiuwen_runtime.service import (
    ExternalIdentity,
    FederationAuthenticationResult,
    FederationConnection,
    FederationError,
    FederationProvider,
)


class DemoFederationProvider(FederationProvider):
    """Simulate a validated enterprise identity for local integration testing.

    This provider deliberately does not accept SAML XML.  Production deployments
    must register a provider that performs complete SAML or OIDC validation.
    """

    def __init__(self, public_path_prefix: str = "/idp") -> None:
        self._public_path_prefix = _normalize_prefix(public_path_prefix)

    async def begin_login(
        self,
        connection: FederationConnection,
        authorization_request_id: str,
    ) -> str:
        query = urlencode(
            {
                "connection_id": connection.connection_id,
                "authorization_request_id": authorization_request_id,
            }
        )
        return f"{self._public_path_prefix}/v1/auth/federation/demo/idp/login?{query}"

    async def consume_callback(
        self,
        connection: FederationConnection,
        parameters: Mapping[str, str],
    ) -> FederationAuthenticationResult:
        request_id = str(parameters.get("authorization_request_id") or "").strip()
        employee_id = str(parameters.get("employee_id") or "").strip()
        display_name = str(parameters.get("display_name") or "").strip()
        email = str(parameters.get("email") or "").strip() or None
        groups = tuple(
            item.strip()
            for item in str(parameters.get("groups") or "").split(",")
            if item.strip()
        )
        if not request_id or not employee_id or not display_name:
            raise FederationError(
                "authorization_request_id, employee_id and display_name are required"
            )
        return FederationAuthenticationResult(
            authorization_request_id=request_id,
            identity=ExternalIdentity(
                connection_id=connection.connection_id,
                issuer=connection.issuer,
                external_subject=employee_id,
                display_name=display_name,
                email=email,
                attributes={
                    "employee_id": employee_id,
                    "email": email,
                    "groups": list(groups),
                },
            ),
        )


def _normalize_prefix(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        return ""
    if not normalized.startswith("/") or normalized.startswith("//"):
        raise ValueError("federation public path prefix must be a relative URL path")
    return normalized


__all__ = ["DemoFederationProvider"]
