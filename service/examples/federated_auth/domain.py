# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Minimal domain objects shared by federation providers and identity stores."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FederationConnection(BaseModel):
    """A trusted enterprise identity connection bound to one local organization."""

    model_config = ConfigDict(frozen=True)

    connection_id: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    organization_id: str = Field(min_length=1)
    organization_name: str = Field(min_length=1)
    default_role: str = Field(default="member", min_length=1)


class ExternalIdentity(BaseModel):
    """Normalized identity produced after an upstream provider is validated."""

    model_config = ConfigDict(frozen=True)

    connection_id: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    external_subject: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    email: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class LocalPrincipal(BaseModel):
    """Local identity consumed by the example's OAuth2 and handler layers."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    organization_id: str
    display_name: str
    email: str | None = None
    roles: tuple[str, ...]
    auth_source: str = "saml"
