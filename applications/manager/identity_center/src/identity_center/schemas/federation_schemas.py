"""Request/response models for browser federation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from identity_center.schemas.auth_schemas import TokenResponse


class FederationAccessToken(BaseModel):
    connection_id: str
    user_id: str
    access_token: str = Field(repr=False)
    token_type: str = "Bearer"


class FederatedTokenResponse(TokenResponse):
    federation: FederationAccessToken | None = None


class FederationConnectionOut(BaseModel):
    connection_id: str
    name: str
    flow: str = "server_code"


class FederationBrowserConfiguration(BaseModel):
    connection_id: str
    flow: str
    authority: str
    client_id: str
    scope: str
    automatic_silent_renew: bool


class FederationConnectionsOut(BaseModel):
    connections: list[FederationConnectionOut]


class FederationCodeExchangeBody(BaseModel):
    code: str = Field(min_length=1, max_length=256)


__all__ = [
    "FederationCodeExchangeBody",
    "FederationConnectionOut",
    "FederationConnectionsOut",
]
