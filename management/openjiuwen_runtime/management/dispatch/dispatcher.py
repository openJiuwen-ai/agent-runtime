# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FastAPI dispatcher entrypoint."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import DispatchSettings
from .exceptions import QueueTimeoutError, SessionHeaderError
from .models import DispatchHeader
from .scheduler import Scheduler
from .store import RedisDispatchStore

WS_HOP_HEADERS = {
    "host",
    "connection",
    "upgrade",
    "sec-websocket-key",
    "sec-websocket-version",
    "sec-websocket-extensions",
}


def create_dispatcher_app(
    settings: DispatchSettings | None = None,
    store: RedisDispatchStore | None = None,
    scheduler: Scheduler | None = None,
) -> FastAPI:
    settings = settings or DispatchSettings.from_env()
    store = store or RedisDispatchStore(settings=settings)
    scheduler = scheduler or Scheduler(store=store, settings=settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await store.connect()
        await scheduler.init()
        yield
        await store.close()

    app = FastAPI(title="OpenJiuwen Runtime Dispatcher", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.scheduler = scheduler

    @app.get("/health")
    async def health() -> JSONResponse:
        redis_ok = await store.ping()
        status = "ok" if redis_ok else "degraded"
        return JSONResponse({"status": status, "redis": redis_ok})

    @app.get("/metrics")
    async def metrics() -> JSONResponse:
        pods = await store.list_pods()
        sessions = await store.list_sessions()
        allocated = sum(pod.allocated for pod in pods)
        capacity = sum(pod.capacity for pod in pods)
        states: dict[str, int] = {}
        for session in sessions:
            states[session.state.value] = states.get(session.state.value, 0) + 1
        utilization = round((allocated / capacity) * 100, 2) if capacity else 0.0
        return JSONResponse(
            {
                "total_pods": len(pods),
                "total_sessions": len(sessions),
                "total_allocated_concurrency": allocated,
                "total_capacity": capacity,
                "utilization_pct": utilization,
                "sessions_by_state": states,
            }
        )

    @app.websocket(settings.dispatcher_ws_path)
    async def ws_entry(client_ws: WebSocket) -> None:
        try:
            header = _parse_session_header(client_ws.headers.get("x-instance-session"))
        except SessionHeaderError as exc:
            await client_ws.close(code=4400, reason=str(exc))
            return

        try:
            target_url = await scheduler.resolve(
                session_id=header.sessionID,
                concurrency=header.concurrency,
                ttl=header.sessionTTL,
            )
        except QueueTimeoutError:
            await client_ws.close(code=4503, reason="queue timeout")
            return

        await client_ws.accept()
        counted = False
        upstream: Any = None
        try:
            await store.incr_ws_count(header.sessionID)
            counted = True
            upstream = await _connect_upstream(
                target_url=target_url,
                ws_path=settings.agent_ws_path,
                headers=_build_upstream_headers(client_ws),
                heartbeat_interval=settings.heartbeat_interval,
            )
            await _pump_bidirectional(client_ws, upstream)
        finally:
            await _safe_close(client_ws, upstream)
            if counted:
                remaining = await store.decr_ws_count(header.sessionID)
                if remaining == 0:
                    await scheduler.enter_ttl_waiting(header.sessionID)

    return app


def _parse_session_header(raw: str | None) -> DispatchHeader:
    if not raw:
        raise SessionHeaderError("missing X-Instance-Session")
    try:
        payload = json.loads(raw)
        return DispatchHeader.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise SessionHeaderError(f"invalid X-Instance-Session: {exc}") from exc


def _build_upstream_headers(client_ws: WebSocket) -> list[tuple[str, str]]:
    return [
        (key, value)
        for key, value in client_ws.headers.items()
        if key.lower() not in WS_HOP_HEADERS
    ]


async def _connect_upstream(
    target_url: str,
    ws_path: str,
    headers: list[tuple[str, str]],
    heartbeat_interval: float,
) -> Any:
    try:
        from websockets.asyncio.client import connect as ws_connect
    except ModuleNotFoundError:
        from websockets import connect as ws_connect  # type: ignore[no-redef]

    upstream_url = f"{target_url.rstrip('/')}{ws_path}"
    kwargs = {
        "ping_interval": heartbeat_interval,
        "ping_timeout": max(heartbeat_interval * 2, 1.0),
        "max_size": None,
    }
    try:
        return await ws_connect(
            upstream_url,
            additional_headers=headers,
            **kwargs,
        )
    except TypeError:  # pragma: no cover - compatibility path for older websockets clients
        return await ws_connect(
            upstream_url,
            extra_headers=headers,
            **kwargs,
        )


async def _pump_bidirectional(client_ws: WebSocket, upstream: Any) -> None:
    async def client_to_upstream() -> None:
        try:
            while True:
                message = await client_ws.receive()
                if message.get("type") == "websocket.disconnect":
                    await upstream.close()
                    return
                data = message.get("bytes")
                if data is not None:
                    await upstream.send(data)
                    continue
                text = message.get("text")
                if text is not None:
                    await upstream.send(text)
        except WebSocketDisconnect:
            await upstream.close()

    async def upstream_to_client() -> None:
        async for message in upstream:
            if isinstance(message, (bytes, bytearray, memoryview)):
                await client_ws.send_bytes(bytes(message))
            else:
                await client_ws.send_text(str(message))

    await asyncio.gather(client_to_upstream(), upstream_to_client(), return_exceptions=True)


async def _safe_close(client_ws: WebSocket, upstream: Any | None) -> None:
    if upstream is not None:
        try:
            await upstream.close()
        except Exception:
            pass
    try:
        await client_ws.close()
    except Exception:
        pass
