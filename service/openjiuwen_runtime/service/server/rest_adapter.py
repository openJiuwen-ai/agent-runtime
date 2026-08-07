# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""REST adapter with one documented endpoint per registered handler."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from typing import Any, Awaitable, Callable, Literal

from fastapi import FastAPI, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SkipValidation, create_model

from ..envelope import Envelope
from ..errors import ErrorCode, http_status_for
from ..routing.handlers import MessageHandler, StreamMessageHandler
from ..routing.result import StreamResult, UnaryResult
from ..routing.router import MessageRouter
from ..security import OAuth2AccessControl


class MetadataBody(BaseModel):
    """OpenAPI request model corresponding to service Metadata."""

    model_config = ConfigDict(extra="ignore")

    request_id: str
    user_id: str | None = None
    chat_id: str | None = None
    session_id: str | None = None
    bot_id: str | None = None
    channel: str | None = None
    timestamp: float | None = None
    trace_id: str | None = None
    instance_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


async def _sse(result: StreamResult):
    try:
        async for chunk in result.chunks:
            yield f"data: {json.dumps(chunk.to_dict(), ensure_ascii=False)}\n\n"
    finally:
        await result.aclose()


async def _wait_for_disconnect(request: Request) -> None:
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


async def _dispatch_with_disconnect(
    router: MessageRouter,
    env: Envelope[Any],
    request_context: Any,
    request: Request,
):
    dispatch_task = asyncio.create_task(
        router.dispatch(env, request_context),
        name=f"rest-dispatch:{request_context.request_id}",
    )
    disconnect_task = asyncio.create_task(
        _wait_for_disconnect(request),
        name=f"rest-disconnect:{request_context.request_id}",
    )
    try:
        done, _ = await asyncio.wait(
            {dispatch_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnect_task in done:
            request_context.interrupt("client disconnected")
            dispatch_task.cancel()
            with suppress(asyncio.CancelledError):
                await dispatch_task
            raise asyncio.CancelledError("client disconnected")
        return dispatch_task.result()
    finally:
        if not disconnect_task.done():
            disconnect_task.cancel()
        with suppress(asyncio.CancelledError):
            await disconnect_task
        if not dispatch_task.done():
            dispatch_task.cancel()
            with suppress(asyncio.CancelledError):
                await dispatch_task


class RestAdapter:
    """Mount registered handlers as explicit FastAPI POST operations."""

    def __init__(
        self,
        fastapi: FastAPI,
        router: MessageRouter,
        prefix: str,
        ensure_sysctx: Callable[[FastAPI], Awaitable[Any]],
        oauth2: OAuth2AccessControl | None = None,
    ) -> None:
        self._fastapi = fastapi
        self._router = router
        self._prefix = prefix
        self._ensure_sysctx = ensure_sysctx
        self._oauth2 = oauth2
        self._registered: set[str] = set()

    def register(
        self,
        handler: MessageHandler[Any] | StreamMessageHandler[Any],
    ) -> None:
        """Mount one handler and invalidate FastAPI's cached OpenAPI schema."""
        spec = handler.spec
        if spec.msg_type in self._registered:
            return
        self._registered.add(spec.msg_type)
        path = self._path_for(spec.msg_type)
        body_model = _envelope_body_model(spec)
        endpoint = self._build_endpoint(body_model)
        endpoint.__name__ = f"service_{_safe_name(spec.msg_type)}"
        endpoint.__annotations__["body"] = body_model

        responses: dict[int | str, dict[str, Any]] | None = None
        if isinstance(handler, StreamMessageHandler):
            responses = {
                200: {
                    "description": "Server-sent event stream",
                    "content": {"text/event-stream": {}},
                }
            }
        elif spec.response_model is not None:
            responses = {
                200: {
                    "description": "Successful response envelope",
                    "model": _response_body_model(spec),
                }
            }

        self._fastapi.add_api_route(
            path,
            endpoint,
            methods=["POST"],
            name=f"service:{spec.msg_type}",
            summary=spec.summary,
            description=spec.description,
            tags=list(spec.tags) or None,
            responses=responses,
        )
        self._fastapi.openapi_schema = None

    def _path_for(self, msg_type: str) -> str:
        suffix = str(msg_type).strip().strip("/")
        return f"{self._prefix}/{suffix}" if self._prefix else f"/{suffix}"

    def _build_endpoint(self, body_model: type[BaseModel]):
        if self._oauth2 is not None:
            auth_dependency = self._oauth2.dependency

            async def endpoint(
                body: body_model,  # noqa: F821
                request: Request,
                principal: Any = Security(auth_dependency),
            ):
                return await self._dispatch(body, request, principal)

            return endpoint

        async def endpoint(body: body_model, request: Request):  # noqa: F821
            return await self._dispatch(body, request, None)

        return endpoint

    async def _dispatch(
        self,
        body: BaseModel,
        request: Request,
        principal: Any,
    ):
        try:
            env = Envelope.from_dict(body.model_dump(mode="python", warnings=False))
        except (KeyError, TypeError, ValueError) as exc:
            return _error_json("", ErrorCode.VALIDATION, f"invalid envelope: {exc}")

        sysctx = await self._ensure_sysctx(self._fastapi)
        request_context = sysctx.for_request(env)
        request_context.principal = principal
        try:
            result = await _dispatch_with_disconnect(
                self._router,
                env,
                request_context,
                request,
            )
        except BaseException:
            await request_context.close()
            raise

        if isinstance(result, UnaryResult):
            try:
                response = result.response
                status = (
                    200
                    if response.ok
                    else http_status_for(response.error_code or ErrorCode.INTERNAL)
                )
                return JSONResponse(response.to_dict(), status_code=status)
            finally:
                await request_context.close()

        try:
            return StreamingResponse(_sse(result), media_type="text/event-stream")
        except BaseException:
            await result.aclose()
            raise


def _envelope_body_model(spec) -> type[BaseModel]:
    # Preserve the request model in OpenAPI while deferring its runtime
    # validation to MessageRouter.  This keeps direct dispatch, REST, and WS on
    # the same validation/error/lifecycle path.
    rawdata_type = (
        SkipValidation[spec.request_model]
        if spec.request_model is not None
        else dict[str, Any]
    )
    name = f"{_safe_name(spec.msg_type)}Envelope"
    return create_model(
        name,
        type=(Literal[spec.msg_type], spec.msg_type),
        metadata=(MetadataBody, ...),
        rawdata=(rawdata_type, ...),
        version=(str, "1"),
    )


def _response_body_model(spec) -> type[BaseModel]:
    name = f"{_safe_name(spec.msg_type)}ResponseEnvelope"
    return create_model(
        name,
        type=(Literal[spec.msg_type], spec.msg_type),
        metadata=(MetadataBody, ...),
        rawdata=(spec.response_model or dict[str, Any], ...),
        ok=(bool, True),
        error_code=(str | None, None),
        error_message=(str | None, None),
        version=(str, "1"),
    )


def _safe_name(value: str) -> str:
    parts = [part for part in re.split(r"[^0-9A-Za-z]+", value) if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts) or "Handler"
    return f"Handler{name}" if name[0].isdigit() else name


def _error_json(msg_type: str, code: str, message: str) -> JSONResponse:
    body = {
        "type": msg_type,
        "metadata": {"request_id": ""},
        "rawdata": {},
        "ok": False,
        "error_code": code,
        "error_message": message,
        "version": "1",
    }
    return JSONResponse(body, status_code=http_status_for(code))


__all__ = ["MetadataBody", "RestAdapter"]
