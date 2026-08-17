# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Transport-neutral contracts for federated identity integration."""

from .coordinator import FederationCoordinator
from .domain import ExternalIdentity, FederationConnection, LocalPrincipal
from .errors import (
    FederationBindingError,
    FederationError,
    UnknownFederationConnection,
)
from .identity_store import FederatedIdentityStore
from .provider import FederationAuthenticationResult, FederationProvider

__all__ = [
    "ExternalIdentity",
    "FederatedIdentityStore",
    "FederationAuthenticationResult",
    "FederationBindingError",
    "FederationConnection",
    "FederationCoordinator",
    "FederationError",
    "FederationProvider",
    "LocalPrincipal",
    "UnknownFederationConnection",
]
