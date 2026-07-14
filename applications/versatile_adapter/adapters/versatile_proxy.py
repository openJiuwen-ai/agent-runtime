# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileProxy — HTTP+SSE 流式调用基类。

封装通用的 HTTP 流式请求 + SSE 行解析 + 错误处理。
子类通过钩子方法定制 URL 构建、请求头过滤、请求体提取、行处理等。
"""
from __future__ import annotations

from typing import AsyncGenerator, Optional

from abc import abstractmethod

import httpx
from loguru import logger

from event.events import (
    AdapterEvent,
)
from adapters.base_adapter import BaseAdapter


class VersatileStreamCtx:
    """SSE 行循环中累积的可变状态，贯穿 _process_line -> _process_chunk -> _on_stream_end。"""

    __slots__ = (
        "completed", "is_failed", "execution_result", "error_message",
        "artifact_texts", "last_artifact_id", "input_required",
    )

    def __init__(self) -> None:
        self.completed: bool = False
        self.is_failed: bool = False
        self.execution_result: str | None = None
        self.error_message: str = ""
        # A2A Gateway 专用：artifact 累积
        self.artifact_texts: dict[str, str] = {}
        self.last_artifact_id: str = ""
        self.input_required: bool = False


class VersatileProxy(BaseAdapter):
    """HTTP+SSE 流式调用基类。

    dispatch_stream 实现完整的 HTTP 流读取 + SSE 解析循环，
    子类通过以下钩子方法定制差异行为：
      - _build_url: URL 模板格式化
      - _build_headers: 请求头构建与过滤
      - _build_request_body: 请求体提取
      - _process_chunk: data 行内容解析，通过 ctx 累积状态，返回事件列表
      - _on_stream_end: 流结束后基于 ctx 的累积状态生成后置事件
    """

    def __init__(
        self,
        url_template: str,
        timeout: int = 600,
        headers_template: Optional[dict] = None,
        forward_header_whitelist: Optional[set[str]] = None,
    ) -> None:
        self._url_template = url_template
        self._timeout = timeout
        self._headers_template = dict(headers_template) if headers_template else {}
        self._forward_header_whitelist = forward_header_whitelist

    # ── 子类可覆盖的钩子 ─────────────────────────────────────

    def _build_url(self, conv_id: str) -> str:
        return self._url_template.format(conversation_id=conv_id)

    def _build_headers(self, headers: Optional[dict] = None) -> dict:
        merged = dict(self._headers_template)
        merged.setdefault("Content-Type", "application/json")
        if headers:
            # 配置中指定仅转发部分HEADER时则仅转发指定的HEADER，否则转发全部HEADER
            if self._forward_header_whitelist:
                merged.update({k: v for k, v in headers.items() if k.lower() in self._forward_header_whitelist})
            else:
                merged.update(headers)
        return merged

    def _build_request_body(self, body: dict) -> dict:
        return body.get("custom_data", {})

    @abstractmethod
    def _process_chunk(self, chunk: str, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        """子类实现：解析 data 行内容，通过 ctx 累积状态，返回事件列表。"""

    def _process_line(self, line: str, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        """SSE 行过滤（跳过 id/event/空行），仅 data 行交由 _process_chunk 处理。"""
        line = line.strip()
        if not line:
            return []
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            return []
        return self._process_chunk(line, ctx)

    @abstractmethod
    def _on_stream_end(self, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        """子类实现：基于 ctx 累积状态生成后置事件。"""

    # ── 主流程 ────────────────────────────────────────────────

    async def dispatch_stream(  # pylint: disable=too-many-arguments
        self,
        conv_id: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        trace_id: str = "",
    ) -> AsyncGenerator[AdapterEvent, None]:
        # 存储入口上下文，供子类钩子方法使用
        self._conv_id = conv_id
        self._trace_id = trace_id
        self._passed_headers = headers or {}

        url = self._build_url(conv_id)
        req_headers = self._build_headers(headers)
        request_body = self._build_request_body(body or {})

        logger.info(f"[VersatileProxy] 发送请求：POST {url}")
        logger.debug(f"[VersatileProxy] 请求头：{req_headers}")
        logger.debug(f"[VersatileProxy] 请求体：{request_body}")

        ctx = VersatileStreamCtx()

        try:
            limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
            timeout = httpx.Timeout(self._timeout, read=None)
            async with httpx.AsyncClient(verify=False, limits=limits, timeout=timeout) as client:
                request = client.build_request("POST", url, json=request_body, headers=req_headers, params=params)
                await self._log_request(request)

                async with client.stream("POST", url, json=request_body, headers=req_headers,
                                         params=params) as response:
                    logger.info(f"[VersatileProxy] Response: {response.status_code}")
                    if response.is_error:
                        body_bytes = await response.aread()
                        body_text = body_bytes.decode("utf-8", errors="replace")
                        logger.error(f"[VersatileProxy] HTTP {response.status_code} url={url} body={body_text!r:.500}")
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        logger.debug(f"[VersatileProxy] received line: {line}")
                        for event in self._process_line(line, ctx):
                            yield event

        except httpx.HTTPStatusError as e:
            logger.error(f"[VersatileProxy] HTTPStatusError：{e.response.status_code} {url}")
            raise
        except httpx.RequestError as e:
            logger.error(f"[VersatileProxy] 请求错误：{e}")
            raise

        for event in self._on_stream_end(ctx):
            yield event

    @staticmethod
    async def _log_request(request: httpx.Request) -> None:
        """记录请求日志（生成 curl 命令）。"""
        _sensitive_headers = {"token", "authorization", "cookie", "set-cookie"}
        body = await request.aread()
        cmd = f"curl -X {request.method} '{request.url}'"
        for key, value in request.headers.items():
            if key.lower() in _sensitive_headers:
                cmd += f" -H '{key}: ***'"
            else:
                cmd += f" -H '{key}: {value}'"
        if body:
            cmd += f" -d '{body.decode('utf-8', errors='replace')}'"
        banner_start = f"{'='*20} Proxy Request (Stream) Start {'='*20}"
        banner_end = f"{'='*20} Proxy Request (Stream) End {'='*20}"
        logger.info("[VersatileProxy] {}", banner_start)
        logger.info("[VersatileProxy] {}", cmd)
        logger.info("[VersatileProxy] {}", banner_end)
