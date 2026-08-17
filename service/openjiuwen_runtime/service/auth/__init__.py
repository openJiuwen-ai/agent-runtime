# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Authentication and identity extension contracts for service applications."""

from .federation import (
    ExternalIdentity,
    FederatedIdentityStore,
    FederationAuthenticationResult,
    FederationBindingError,
    FederationConnection,
    FederationCoordinator,
    FederationError,
    FederationProvider,
    LocalPrincipal,
    UnknownFederationConnection,
)

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
