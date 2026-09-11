# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ASGI link authorization for new requests AND in-flight response streams.

Authorization is checked before every send and while an application is idle.
Revocation cancels the response producer, allowing its finally blocks to run.
No polling request, synthetic SSE event or caller-supplied certificate header.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from types import SimpleNamespace

from .link_profile import LinkProfileError

logger = logging.getLogger(__name__)


class LinkBindingMiddleware:
    def __init__(self, app, *, authorize, check_interval: float = 0.5):
        if not math.isfinite(check_interval) or check_interval <= 0:
            raise ValueError("link authorization interval must be positive and finite")
        self.app = app
        self.authorize = authorize
        self.check_interval = check_interval

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = SimpleNamespace(
            scope=scope,
            headers={k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])},
        )
        reason = None
        revoked = asyncio.Event()
        started = False
        finished = False
        content_length = None
        sent_bytes = 0
        lock = asyncio.Lock()

        def valid():
            nonlocal reason
            try:
                self.authorize(request)
                return True
            except (ValueError, OSError) as exc:
                reason = str(exc)
                revoked.set()
                return False

        async def reject():
            body = json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "LINK_BINDING_MISMATCH",
                        "message": reason,
                    },
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})

        if not valid():
            logger.warning("rejected link binding: %s", reason)
            return await reject()

        async def guarded_send(message):
            nonlocal started, finished, content_length, sent_bytes
            async with lock:
                if revoked.is_set() or not valid():
                    # The watcher cancels the producer; do not let an inner
                    # exception handler replace this with a successful response.
                    await asyncio.Future()
                await send(message)
                if message["type"] == "http.response.start":
                    started = True
                    for key, value in message.get("headers", []):
                        if key.lower() == b"content-length":
                            content_length = int(value)
                elif message["type"] == "http.response.body":
                    sent_bytes += len(message.get("body", b""))
                    finished = not message.get("more_body", False)

        async def watch():
            while not revoked.is_set():
                try:
                    await asyncio.wait_for(revoked.wait(), self.check_interval)
                except TimeoutError:
                    valid()

        application = asyncio.create_task(self.app(scope, receive, guarded_send))
        watcher = asyncio.create_task(watch())
        try:
            await asyncio.wait({application, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if revoked.is_set():
                application.cancel()
                await asyncio.gather(application, return_exceptions=True)
                logger.warning("terminated in-flight link response: %s", reason)
                async with lock:
                    if not started:
                        await reject()
                    elif not finished:
                        if content_length is not None and sent_bytes < content_length:
                            # Fixed-length downloads cannot be turned into a
                            # successful short response. Let the ASGI server
                            # abort the connection instead of sending bad framing.
                            raise LinkProfileError("link authorization revoked during response")
                        # Cannot send another HTTP status after response.start.
                        # End the stream without sending any further app data.
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
            else:
                await application
        finally:
            for task in (application, watcher):
                if not task.done():
                    task.cancel()
            await asyncio.gather(application, watcher, return_exceptions=True)
