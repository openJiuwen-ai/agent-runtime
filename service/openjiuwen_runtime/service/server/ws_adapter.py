# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""WebSocket 适配器（设计 §7.2）。

单一 ``/ws``，每条入站文本帧 = 一个 Envelope JSON；非流式 → 回一帧，流式 → 回 N 帧
（末帧 ``is_final=True``）。每连接一把 ``send_lock`` 防并发写。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ..envelope import Envelope
from ..routing.result import UnaryResult


def _error_frame(message: str, code: str = "validation") -> str:
    return json.dumps({
        "type": "",
        "metadata": {"request_id": ""},
        "rawdata": {},
        "ok": False,
        "error_code": code,
        "error_message": message,
        "version": "1",
    }, ensure_ascii=False)


def mount_ws(
    fastapi: FastAPI,
    router: Any,
    ensure_sysctx: Callable[[FastAPI], Awaitable[Any]],
) -> None:
    @fastapi.websocket("/ws")
    async def handler(ws: WebSocket):
        await ws.accept()
        send_lock = asyncio.Lock()
        sysctx = await ensure_sysctx(fastapi)
        try:
            while True:
                text = await ws.receive_text()
                try:
                    env = Envelope.from_dict(json.loads(text))
                except (KeyError, TypeError, ValueError):
                    async with send_lock:
                        await ws.send_text(_error_frame("invalid envelope frame"))
                    continue

                rctx = sysctx.for_request(env.metadata)
                result = await router.dispatch(env, rctx)
                if isinstance(result, UnaryResult):
                    async with send_lock:
                        await ws.send_text(json.dumps(result.response.to_dict(), ensure_ascii=False))
                else:
                    async for chunk in result.chunks:
                        async with send_lock:
                            await ws.send_text(json.dumps(chunk.to_dict(), ensure_ascii=False))
        except WebSocketDisconnect:
            return
