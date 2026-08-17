# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Asynchronous boundary around an external identity protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping

from .domain import ExternalIdentity, FederationConnection


@dataclass(frozen=True)
class FederationAuthenticationResult:
    """Validated external identity and the local authorization request it serves."""

    authorization_request_id: str
    identity: ExternalIdentity


class FederationProvider(ABC):
    """Validate an upstream protocol and return a normalized external identity."""

    @abstractmethod
    async def begin_login(
        self,
        connection: FederationConnection,
        authorization_request_id: str,
    ) -> str:
        """Return the upstream login URL for one authorization request."""
        raise NotImplementedError

    @abstractmethod
    async def consume_callback(
        self,
        connection: FederationConnection,
        parameters: Mapping[str, str],
    ) -> FederationAuthenticationResult:
        """Validate an upstream callback and normalize its trusted identity."""
        raise NotImplementedError


__all__ = ["FederationAuthenticationResult", "FederationProvider"]
