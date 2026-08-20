# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Reusable identity components for the federated-auth example."""

from openjiuwen_runtime.service.auth.federation import (
    ExternalIdentity,
    FederatedIdentityStore,
    FederationAuthenticationResult,
    FederationConnection,
    FederationCoordinator,
    FederationProvider,
    LocalPrincipal,
)

from .database_identity_store import DatabaseFederatedIdentityStore
from .demo_idp import DemoEnterpriseIdentityProvider
from .identity_store import InMemoryFederatedIdentityStore
from .module import FederatedAuthModule
from .oauth2_server import ExampleOAuth2AuthorizationServer
from .provider import DemoFederationProvider

__all__ = [
    "DatabaseFederatedIdentityStore",
    "DemoEnterpriseIdentityProvider",
    "DemoFederationProvider",
    "ExampleOAuth2AuthorizationServer",
    "ExternalIdentity",
    "FederatedIdentityStore",
    "FederationAuthenticationResult",
    "FederatedAuthModule",
    "FederationProvider",
    "FederationConnection",
    "FederationCoordinator",
    "InMemoryFederatedIdentityStore",
    "LocalPrincipal",
]
