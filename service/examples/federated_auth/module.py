# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Mount a federation provider into the example's local OAuth2 server."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .domain import FederationConnection
from .identity_store import FederatedIdentityStore
from .oauth2_server import ExampleOAuth2AuthorizationServer, OAuth2FlowError
from .provider import FederationProvider


class FederatedAuthModule:
    """Coordinate upstream federation, local shadow identity, and OAuth2."""

    def __init__(
        self,
        *,
        provider: FederationProvider,
        identity_store: FederatedIdentityStore,
        oauth2_server: ExampleOAuth2AuthorizationServer,
        connections: Mapping[str, FederationConnection],
    ) -> None:
        self._provider = provider
        self._identity_store = identity_store
        self._oauth2_server = oauth2_server
        self._connections = dict(connections)

    def mount(self, fastapi: FastAPI) -> None:
        """Mount browser redirect and callback routes on one FastAPI app."""

        @fastapi.get("/auth/federation/{connection_id}/login", tags=["federation"])
        async def begin_federated_login(
            connection_id: str,
            authorization_request_id: str,
        ):
            connection = self._connections.get(connection_id)
            if connection is None:
                return JSONResponse(
                    {"detail": "unknown federation connection"},
                    status_code=404,
                )
            try:
                await self._oauth2_server.require_authorization_request(
                    authorization_request_id
                )
                login_url = await self._provider.begin_login(
                    connection,
                    authorization_request_id,
                )
            except (OAuth2FlowError, ValueError) as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
            return RedirectResponse(login_url, status_code=303)

        @fastapi.post(
            "/auth/federation/{connection_id}/callback",
            tags=["federation"],
        )
        async def complete_federated_login(connection_id: str, request: Request):
            connection = self._connections.get(connection_id)
            if connection is None:
                return JSONResponse(
                    {"detail": "unknown federation connection"},
                    status_code=404,
                )
            form_data = await request.form()
            form = {key: str(value) for key, value in form_data.items()}
            try:
                result = await self._provider.consume_callback(connection, form)
                await self._oauth2_server.require_authorization_request(
                    result.authorization_request_id
                )
                principal = await self._identity_store.resolve_or_create(
                    connection,
                    result.identity,
                )
                redirect_url = await self._oauth2_server.complete_authorization(
                    result.authorization_request_id,
                    principal,
                )
            except (OAuth2FlowError, ValueError) as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
            return RedirectResponse(redirect_url, status_code=303)
