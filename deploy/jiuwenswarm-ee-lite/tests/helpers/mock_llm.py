# coding: utf-8
"""Mock LLM：OpenAI 兼容 /v1/chat/completions 假端点（e2e 用）。

agentserver 拉模型配置后按 OpenAI 协议请求 base_url；本 mock 固定返回
"mock-llm-reply" 前缀回复 + 可注入的按序台词，测试据此断言链路打通。

线程内 uvicorn 起服务，阻塞端口随测试进程生命周期：
    with MockLLM() as llm:
        llm.base_url        # http://127.0.0.1:<port>/v1  ← 模型模板 base_url 填这个
        llm.requests        # 收到的请求体列表（断言 prompt 经过了 agentserver）
        llm.set_replies(["a", "b"])   # 按序台词（默认 mock-llm-reply-<n>）
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class MockLLM:
    """OpenAI Chat Completions 兼容假端点。"""

    port: int = field(default_factory=_free_port)
    requests: list[dict[str, Any]] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    _srv: uvicorn.Server | None = None
    _thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def set_replies(self, replies: list[str]) -> None:
        """按序台词；队列空了回落 mock-llm-reply-<序号>。"""
        self.replies.clear()
        self.replies.extend(replies)

    # -------------------------------------------------------------- 生命周期
    def start(self) -> None:
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            body = await request.json()
            self.requests.append(body)
            n = len(self.requests)
            reply = (
                self.replies.pop(0) if self.replies
                else f"mock-llm-reply-{n}")
            model = (body or {}).get("model", "mock-model")
            created = int(time.time())
            base = {
                "id": f"chatcmpl-mock-{n}",
                "object": "chat.completion",
                "created": created,
                "model": model,
            }
            # 流式请求（OpenAI SSE：data: chunk 行 + data: [DONE]）——agentserver
            # 模型配置默认 enable_streaming,非流式 JSON 会被解析成空内容。
            if (body or {}).get("stream"):
                async def sse() -> AsyncIterator[str]:
                    def chunk(delta_content: str | None, finish: str | None) -> str:
                        return "data: " + json.dumps({
                            **base,
                            "object": "chat.completion.chunk",
                            "choices": [{
                                "index": 0,
                                "delta": (
                                    {"role": "assistant"}
                                    if delta_content is None
                                    else {"content": delta_content}),
                                **({"finish_reason": finish} if finish else {}),
                            }],
                        }, ensure_ascii=False) + "\n\n"

                    yield chunk(None, None)          # role 首块
                    for i in range(0, len(reply), 32):
                        yield chunk(reply[i:i + 32], None)
                    yield chunk("", "stop")
                    yield "data: [DONE]\n\n"

                return StreamingResponse(
                    sse(), media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache"})
            return JSONResponse({
                **base,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                },
            })

        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port,
            log_level="warning", access_log=False,
        )
        self._srv = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._srv.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self._srv.started:
                return
            time.sleep(0.05)
        raise RuntimeError("mock LLM did not start within 15s")

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._srv = None
        self._thread = None

    def __enter__(self) -> "MockLLM":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -------------------------------------------------------------- 断言辅助
    def last_user_text(self) -> str:
        """最后一条请求的最后一条 user 消息文本（链路穿透断言用）。"""
        body = self.requests[-1]
        for msg in reversed(body.get("messages", [])):
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return json.dumps(content, ensure_ascii=False)
        return ""
