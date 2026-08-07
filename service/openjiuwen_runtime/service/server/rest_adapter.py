# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""REST 适配器（设计 §7.1）。

单一参数化路由 ``POST /{prefix}/{msg_type}``，body = 完整 Envelope；非流式 → JSON
ResponseEnvelope，流式 → SSE（每个 StreamChunk 一个 data: 事件）。与 body 冲突时以
body 中的 ``type`` 为准。仅此层 import fastapi。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..envelope import Envelope
from ..errors import ErrorCode, http_status_for
from ..routing.result import StreamResult, UnaryResult
from ..routing.router import MessageRouter


def _error_json(msg_type: str, code: str, message: str, request_id: str = "") -> JSONResponse:
    body = {
        "type": msg_type,
        "metadata": {"request_id": request_id},
        "rawdata": {},
        "ok": False,
        "error_code": code,
        "error_message": message,
        "version": "1",
    }
    return JSONResponse(body, status_code=http_status_for(code))


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
    env: Envelope,
    rctx: Any,
    request: Request,
):
    dispatch_task = asyncio.create_task(
        router.dispatch(env, rctx), name=f"rest-dispatch:{rctx.request_id}"
    )
    disconnect_task = asyncio.create_task(
        _wait_for_disconnect(request), name=f"rest-disconnect:{rctx.request_id}"
    )
    try:
        done, _ = await asyncio.wait(
            {dispatch_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnect_task in done:
            rctx.interrupt("client disconnected")
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


def mount_rest(
    fastapi: FastAPI,
    router: MessageRouter,
    prefix: str,
    ensure_sysctx: Callable[[FastAPI], Awaitable[Any]],
) -> None:
    path = f"{prefix}/{{msg_type}}" if prefix else "/{msg_type}"

    @fastapi.post(path)
    async def handler(msg_type: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            return _error_json(msg_type, ErrorCode.VALIDATION, "invalid JSON body")

        try:
            env = Envelope.from_dict(body)
        except (KeyError, TypeError) as exc:
            return _error_json(msg_type, ErrorCode.VALIDATION, f"invalid envelope: {exc}")

        sysctx = await ensure_sysctx(fastapi)
        rctx = sysctx.for_request(env)
        try:
            result = await _dispatch_with_disconnect(router, env, rctx, request)
        except BaseException:
            await rctx.close()
            raise

        if isinstance(result, UnaryResult):
            try:
                resp = result.response
                status = 200 if resp.ok else http_status_for(
                    resp.error_code or ErrorCode.INTERNAL
                )
                return JSONResponse(resp.to_dict(), status_code=status)
            finally:
                await rctx.close()

        # 流式 → SSE
        try:
            return StreamingResponse(_sse(result), media_type="text/event-stream")
        except BaseException:
            await result.aclose()
            raise
