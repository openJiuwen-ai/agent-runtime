# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Errors raised by transport-neutral federation orchestration."""


class FederationError(ValueError):
    """Base error for invalid federation requests or trusted configuration."""


class UnknownFederationConnection(FederationError):
    """Raised when a request names a connection that is not configured."""


class FederationBindingError(FederationError):
    """Raised when an external identity does not match its trusted connection."""


__all__ = [
    "FederationBindingError",
    "FederationError",
    "UnknownFederationConnection",
]
