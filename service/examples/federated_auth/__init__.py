# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Reusable identity components for the federated-auth example."""

from .database_identity_store import DatabaseFederatedIdentityStore
from .demo_idp import DemoEnterpriseIdentityProvider
from .domain import ExternalIdentity, FederationConnection, LocalPrincipal
from .identity_store import FederatedIdentityStore, InMemoryFederatedIdentityStore
from .module import FederatedAuthModule
from .oauth2_server import ExampleOAuth2AuthorizationServer
from .provider import DemoFederationProvider, FederationProvider

__all__ = [
    "DatabaseFederatedIdentityStore",
    "DemoEnterpriseIdentityProvider",
    "DemoFederationProvider",
    "ExampleOAuth2AuthorizationServer",
    "ExternalIdentity",
    "FederatedIdentityStore",
    "FederatedAuthModule",
    "FederationProvider",
    "FederationConnection",
    "InMemoryFederatedIdentityStore",
    "LocalPrincipal",
]
