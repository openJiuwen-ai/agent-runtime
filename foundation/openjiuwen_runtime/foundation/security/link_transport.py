# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TLS peer extraction at the transport boundary, before HTTP business data."""

from __future__ import annotations

import httpx
from uvicorn.protocols.http.h11_impl import H11Protocol

from .link_profile import PEER_CERT_SCOPE, LinkProfileError


class PeerCertificateH11Protocol(H11Protocol):
    """Per-connection scope attachment; no client header can set this value."""

    def connection_made(self, transport):
        super().connection_made(transport)
        ssl_object = transport.get_extra_info("ssl_object")
        peer = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        app = self.app

        async def with_peer(scope, receive, send):
            scope = dict(scope)
            scope[PEER_CERT_SCOPE] = peer
            await app(scope, receive, send)

        self.app = with_peer


class PinnedAsyncTransport(httpx.AsyncBaseTransport):
    """Enforce pin verification BEFORE sending headers/body.

    A per-request transport intentionally avoids reusing connections across
    profile revocation/rotation. SSE stays on its connection until completion.
    Keep-alive pooling can be added only with equivalent pre-write verification.
    """

    def __init__(
        self,
        profile,
        role,
        *,
        expected_fingerprints=None,
        ca_file=None,
    ):
        self.profile, self.role = profile, role
        self.expected_fingerprints = expected_fingerprints
        self.ca_file = ca_file

    async def handle_async_request(self, request):
        self.profile.current()
        if request.url.scheme != "https":
            raise LinkProfileError("pinned transport refuses plaintext HTTP")
        checked = False
        previous_trace = request.extensions.get("trace")

        async def trace(name, info):
            nonlocal checked
            if name == "connection.start_tls.complete":
                stream = info["return_value"]
                ssl_object = stream.get_extra_info("ssl_object")
                try:
                    self.profile.verify_peer(
                        ssl_object.getpeercert(binary_form=True)
                        if ssl_object
                        else None,
                        {self.role},
                        expected_fingerprints=self.expected_fingerprints,
                    )
                    checked = True
                except Exception:
                    await stream.aclose()
                    raise
            if name.endswith("send_request_headers.started") and not checked:
                raise LinkProfileError(
                    "transport did not expose an authenticated TLS peer"
                )
            if previous_trace is not None:
                await previous_trace(name, info)

        request.extensions["trace"] = trace
        transport = httpx.AsyncHTTPTransport(
            verify=self.profile.ssl_context(ca_file=self.ca_file),
            trust_env=False,
            http2=False,
            limits=httpx.Limits(max_keepalive_connections=0),
            retries=0,
        )
        try:
            response = await transport.handle_async_request(request)
        except BaseException:
            await transport.aclose()
            raise
        response.stream = _ProfileStream(response.stream, transport, self.profile)
        return response


class _ProfileStream(httpx.AsyncByteStream):
    def __init__(self, stream, transport, profile):
        self.stream, self.transport, self.profile = stream, transport, profile

    async def __aiter__(self):
        async for chunk in self.stream:
            self.profile.current()
            yield chunk

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            await self.transport.aclose()
