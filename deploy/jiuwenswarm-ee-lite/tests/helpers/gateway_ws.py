# coding: utf-8
"""gateway Web 通道 WS 客户端（e2e 用户面模拟）。

协议（gateway/channel_manager/web/web_connect.py，JSON-RPC 风格）：
- 请求:``{"type":"req","id":<id>,"method":"chat.send","params":{...}}``
- 应答:``{"type":"res","id":<id>,"ok":bool,"payload":{...}}``
- 事件:``{"type":"event","event":"chat.delta|chat.final|chat.error|...",
  "payload":{"session_id","content","request_id",...}}``（request_id 回填
  请求 id,用于关联）
- 身份走握手 query:``?user_id=&group_id=&bot_id=``（runtime 模式无需 token）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, AsyncIterator

import websockets


class GatewayWsClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._ws: websockets.WebSocketClientProtocol | None = None

    async def __aenter__(self) -> "GatewayWsClient":
        self._ws = await websockets.connect(self.url, open_timeout=30)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._ws = None

    async def send(self, frame: dict[str, Any]) -> None:
        assert self._ws is not None
        await self._ws.send(json.dumps(frame, ensure_ascii=False))

    async def recv(self, timeout: float = 30.0) -> dict[str, Any]:
        assert self._ws is not None
        raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        return json.loads(raw)

    async def chat_send(
        self, *, session_id: str, content: str, req_id: str, timeout: float = 30.0,
    ) -> dict[str, Any]:
        """发 chat.send 并等到对应 res（accepted 与否）。"""
        await self.send({
            "type": "req", "id": req_id, "method": "chat.send",
            "params": {"session_id": session_id, "content": content, "mode": "agent"},
        })
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no res for {req_id} within {timeout}s")
            frame = await self.recv(timeout=remaining)
            if frame.get("type") == "res" and frame.get("id") == req_id:
                return frame
            # 其它帧（connection.ack 等）跳过

    async def until_final(
        self, *, req_id: str, timeout: float = 300.0,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式收集事件直到 chat.final / chat.error（含 request_id 关联）。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no chat.final for {req_id} within {timeout}s")
            frame = await self.recv(timeout=remaining)
            if frame.get("type") != "event":
                continue
            payload = frame.get("payload") or {}
            # 未回填 request_id 的栈内事件（如 processing_status）按 session 宽容放行
            if payload.get("request_id") not in (None, req_id):
                continue
            yield frame
            if frame.get("event") in ("chat.final", "chat.error"):
                return
