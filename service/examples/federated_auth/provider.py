# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Local demonstration provider for the formal federation contract."""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlencode

from openjiuwen_runtime.service.auth.federation import (
    ExternalIdentity,
    FederationAuthenticationResult,
    FederationConnection,
    FederationProvider,
)


class DemoFederationProvider(FederationProvider):
    """Local provider used to exercise federation without pretending to verify SAML."""

    async def begin_login(
        self,
        connection: FederationConnection,
        authorization_request_id: str,
    ) -> str:
        query = urlencode(
            {
                "connection_id": connection.connection_id,
                "authorization_request_id": authorization_request_id,
            }
        )
        return f"/demo-enterprise-idp/login?{query}"

    async def consume_callback(
        self,
        connection: FederationConnection,
        form: Mapping[str, str],
    ) -> FederationAuthenticationResult:
        authorization_request_id = _required(form, "authorization_request_id")
        employee_id = _required(form, "employee_id")
        display_name = _required(form, "display_name")
        email = str(form.get("email") or "").strip() or None
        return FederationAuthenticationResult(
            authorization_request_id=authorization_request_id,
            identity=ExternalIdentity(
                connection_id=connection.connection_id,
                issuer=connection.issuer,
                external_subject=employee_id,
                display_name=display_name,
                email=email,
                attributes={"employee_id": employee_id},
            ),
        )


def _required(form: Mapping[str, str], name: str) -> str:
    value = str(form.get(name) or "").strip()
    if not value:
        raise ValueError(f"missing required federation field: {name}")
    return value


__all__ = [
    "DemoFederationProvider",
    "FederationAuthenticationResult",
    "FederationProvider",
]
