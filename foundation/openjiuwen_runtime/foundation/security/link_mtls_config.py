# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared link mode and binding metadata; headers never authorize a TLS peer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .link_profile import LinkProfileError

MODE_ENV = "JIUWENSWARM_LINK_MTLS_MODE"
CA_FILE_ENV = "JIUWENSWARM_LINK_MTLS_CA_FILE"
CERT_FILE_ENV = "JIUWENSWARM_LINK_MTLS_CERT_FILE"
KEY_FILE_ENV = "JIUWENSWARM_LINK_MTLS_KEY_FILE"
MTLS_BINDING_ID_HEADER = "X-Jiuwenswarm-Mtls-Binding-Id"
MTLS_BINDING_EPOCH_HEADER = "X-Jiuwenswarm-Mtls-Binding-Epoch"


LinkMTLSError = LinkProfileError


class LinkMTLSMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


@dataclass(frozen=True)
class MTLSDeploymentIdentity:
    """Deployment-owned identity loaded from the installed mTLS profile."""

    mtls_deployment_id: str
    mtls_binding_id: str
    mtls_binding_epoch: int

    def headers(self) -> dict[str, str]:
        return {
            MTLS_BINDING_ID_HEADER: self.mtls_binding_id,
            MTLS_BINDING_EPOCH_HEADER: str(self.mtls_binding_epoch),
        }

    def validate_headers(self, headers: Mapping[str, str]) -> None:
        actual = {
            MTLS_BINDING_ID_HEADER: headers.get(MTLS_BINDING_ID_HEADER, ""),
            MTLS_BINDING_EPOCH_HEADER: headers.get(MTLS_BINDING_EPOCH_HEADER, ""),
        }
        expected = self.headers()
        for name, value in expected.items():
            if actual.get(name) != value:
                raise LinkMTLSError(
                    f"link binding mismatch for {name}: expected {value!r}"
                )
