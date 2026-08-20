"""Request/response models for browser federation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FederationConnectionOut(BaseModel):
    connection_id: str
    name: str


class FederationConnectionsOut(BaseModel):
    connections: list[FederationConnectionOut]


class FederationCodeExchangeBody(BaseModel):
    code: str = Field(min_length=1, max_length=256)


__all__ = [
    "FederationCodeExchangeBody",
    "FederationConnectionOut",
    "FederationConnectionsOut",
]
