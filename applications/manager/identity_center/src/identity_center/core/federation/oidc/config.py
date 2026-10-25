"""Declarative OIDC connections; customer values stay outside protocol code."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from identity_center.infrastructure.config import Settings


class OidcConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    connection_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(default="Enterprise SSO", min_length=1, max_length=128)
    flow: Literal["server_code", "browser_pkce"] = "browser_pkce"
    issuer: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: SecretStr | None = None
    redirect_uri: str = ""
    organization_id: str = Field(min_length=1, max_length=64)
    access_token_audience: str = Field(min_length=1)
    scope: str = "openid email profile"
    ca_bundle: str | None = None
    handoff_access_token: bool = True
    automatic_silent_renew: bool = False
    # Explicit compatibility option for issuers issuing non-expiring tokens.
    require_exp: bool = True
    claim_mapping: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_connection(self):
        self.issuer = self.issuer.strip().rstrip("/")
        if "openid" not in self.scope.split():
            raise ValueError("OIDC scope must contain openid")
        allowed = {"display_name", "preferred_username", "email", "groups"}
        if set(self.claim_mapping) - allowed or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", value)
            for value in self.claim_mapping.values()
        ):
            raise ValueError("unsupported OIDC claim mapping")
        has_secret = bool(self.client_secret and self.client_secret.get_secret_value())
        if self.flow == "server_code" and (not self.redirect_uri or not has_secret):
            raise ValueError("server_code requires redirect_uri and client_secret")
        if self.flow == "browser_pkce" and self.client_secret is not None:
            raise ValueError("browser_pkce uses a public client without a secret")
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> OidcConnectionConfig:
        """Single-connection environment deployments, preserving the legacy flow."""
        sso = {
            "IDENTITY_SSO_SERVER_URL": settings.sso_server_url.strip(),
            "IDENTITY_SSO_LOGIN_CLIENT_ID": settings.sso_login_client_id.strip(),
            "IDENTITY_SSO_TOKEN_AUDIENCE": settings.sso_token_audience.strip(),
        }
        if any(sso.values()):
            missing = [name for name, value in sso.items() if not value]
            if missing:
                raise ValueError(
                    "Enterprise SSO requires all three settings; missing: "
                    + ", ".join(missing)
                )
            return cls(
                connection_id=settings.federation_oidc_connection_id,
                name=settings.federation_oidc_name,
                flow="browser_pkce",
                issuer=sso["IDENTITY_SSO_SERVER_URL"],
                client_id=sso["IDENTITY_SSO_LOGIN_CLIENT_ID"],
                organization_id=settings.federation_oidc_organization_id,
                access_token_audience=sso["IDENTITY_SSO_TOKEN_AUDIENCE"],
            )
        return cls(
            connection_id=settings.federation_oidc_connection_id,
            name=settings.federation_oidc_name,
            flow=settings.federation_oidc_flow,
            issuer=settings.federation_oidc_issuer,
            client_id=settings.federation_oidc_client_id,
            client_secret=settings.federation_oidc_client_secret,
            redirect_uri=settings.federation_oidc_redirect_uri,
            organization_id=settings.federation_oidc_organization_id,
            access_token_audience=settings.federation_oidc_audience,
            ca_bundle=settings.federation_oidc_ca_bundle,
            require_exp=settings.federation_oidc_flow == "browser_pkce",
        )
