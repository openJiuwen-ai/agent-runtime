# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""REST 适配器（设计 §7.1）。

单一参数化路由 ``POST /{prefix}/{msg_type}``，body = 完整 Envelope；非流式 → JSON
ResponseEnvelope，流式 → SSE（每个 StreamChunk 一个 data: 事件）。与 body 冲突时以
body 中的 ``type`` 为准。仅此层 import fastapi。
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..envelope import Envelope
from ..errors import ErrorCode, http_status_for
from ..routing.result import UnaryResult
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


async def _sse(chunks):
    async for chunk in chunks:
        yield f"data: {json.dumps(chunk.to_dict(), ensure_ascii=False)}\n\n"


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
        rctx = sysctx.for_request(env.metadata)
        result = await router.dispatch(env, rctx)

        if isinstance(result, UnaryResult):
            resp = result.response
            status = 200 if resp.ok else http_status_for(resp.error_code or ErrorCode.INTERNAL)
            return JSONResponse(resp.to_dict(), status_code=status)

        # 流式 → SSE
        return StreamingResponse(_sse(result.chunks), media_type="text/event-stream")
