# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Persistence contract for stable external-to-local identity mappings."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import ExternalIdentity, FederationConnection, LocalPrincipal
from .errors import FederationBindingError


class FederatedIdentityStore(ABC):
    """Resolve validated external identities into stable local principals."""

    @abstractmethod
    async def resolve_or_create(
        self,
        connection: FederationConnection,
        identity: ExternalIdentity,
    ) -> LocalPrincipal:
        """Return an existing principal or create its local shadow records."""
        raise NotImplementedError

    @abstractmethod
    async def find(
        self,
        *,
        connection_id: str,
        issuer: str,
        external_subject: str,
    ) -> LocalPrincipal | None:
        """Find a principal by its stable external identity key."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release resources owned by this store."""
        raise NotImplementedError

    @staticmethod
    def validate_binding(
        connection: FederationConnection,
        identity: ExternalIdentity,
    ) -> None:
        """Ensure an identity can only be consumed by its trusted connection."""
        if identity.connection_id != connection.connection_id:
            raise FederationBindingError(
                "external identity connection_id does not match connection"
            )
        if identity.issuer != connection.issuer:
            raise FederationBindingError(
                "external identity issuer does not match trusted issuer"
            )


__all__ = ["FederatedIdentityStore"]
