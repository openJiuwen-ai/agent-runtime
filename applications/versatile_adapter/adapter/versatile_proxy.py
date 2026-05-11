# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileProxy — 通过 HTTP 流式调用 Versatile 低代码平台（NDJSON 协议）。
"""
from __future__ import annotations

import json as _json
from typing import AsyncGenerator, Optional

import httpx
from loguru import logger


_FORWARD_HEADER_WHITELIST = {
    "x-user-id", "x-project-id", "cust-token", "cust-userid",
    # 允许上游动态覆盖模板里的 Cookie（用户级 AGENT_SID 等）
    "cookie",
    # 用于端到端的分布式追踪
    "x-trace-id",
}


def _unwrap_upstream_frame(outer: dict) -> dict:
    """从上游 Versatile NDJSON 行提取工作流事件载荷。

    兼容两种上游形状：
      1. 带 envelope：``{..., "custom_rsp_data": {"event": "<kind>", "data": {...}}}``
         （真实 Versatile 网关常见）
      2. 已解包直发：``{"event": "<kind>", "data": {...}}``
         （简化网关 / 内部 mock）

    返回：``{"event": "<kind>", "data": {...}}``，供下游 wrap_workflow_event 使用。

    上游异常帧回退为 ``{"event": "message", "data": <raw or {}>}``，
    把原始载荷交给下游兜底（不会二次套壳）。
    """
    if not isinstance(outer, dict):
        return {"event": "message", "data": {}}

    # 形态 1：带 custom_rsp_data envelope
    inner = outer.get("custom_rsp_data")
    if isinstance(inner, dict) and "event" in inner:
        return {"event": inner["event"], "data": inner.get("data") or {}}

    # 形态 2：上游已直发 {event, data}
    if "event" in outer:
        data = outer.get("data")
        return {
            "event": outer["event"],
            "data": data if isinstance(data, dict) else {},
        }

    # 无法识别：整体当 message 帧的 data 兜底
    return {"event": "message", "data": outer}


class VersatileProxy:
    def __init__(
        self,
        url_template: str,
        timeout: int = 600,
        headers_template: Optional[dict] = None,
    ) -> None:
        self._url_template = url_template
        self._timeout = timeout
        self._headers_template = dict(headers_template) if headers_template else {}

    def _build_url(self, conv_id: str) -> str:
        return self._url_template.format(conversation_id=conv_id)

    @staticmethod
    def _generate_curl_command(request: httpx.Request, body: bytes) -> str:
        """生成 curl 命令用于调试。"""
        cmd = f"curl -X {request.method} '{request.url}'"
        for key, value in request.headers.items():
            cmd += f" -H '{key}: {value}'"
        if body:
            try:
                json_body = _json.loads(body.decode('utf-8'))
                cmd += f" -d '{_json.dumps(json_body, ensure_ascii=False)}'"
            except Exception:
                logger.debug("[VersatileProxy] body 非 JSON，curl 命令使用 raw 字节回退")
                cmd += f" -d '{body.decode('utf-8', errors='replace')}'"
        return cmd

    async def _log_request(self, request: httpx.Request) -> None:
        """记录请求日志（生成 curl 命令）。"""
        body = await request.aread()
        curl = self._generate_curl_command(request, body)
        banner_start = f"{'='*20} Proxy Request (Stream) Start {'='*20}"
        banner_end = f"{'='*20} Proxy Request (Stream) End {'='*20}"
        logger.info("[VersatileProxy] {}", banner_start)
        logger.info("[VersatileProxy] {}", curl)
        logger.info("[VersatileProxy] {}", banner_end)

    async def dispatch_stream(
        self,
        body: dict,
        conv_id: str,
        extra_headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> AsyncGenerator[dict, None]:
        url = self._build_url(conv_id)
        headers = dict(self._headers_template)
        headers.setdefault("Content-Type", "application/json")
        if extra_headers:
            headers.update(
                {k: v for k, v in extra_headers.items() if k.lower() in _FORWARD_HEADER_WHITELIST}
            )

        logger.info(f"[VersatileProxy] 发送请求：POST {url}")
        logger.debug(f"[VersatileProxy] 请求头：{headers}")
        logger.debug(f"[VersatileProxy] 请求体：{body.get('custom_data', {})})")
        logger.debug(f"[VersatileProxy] 请求参数：{params})")

        try:
            async with httpx.AsyncClient(
                verify=False,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                timeout=httpx.Timeout(self._timeout, read=None),
            ) as client:
                # 构建请求对象用于日志
                custom_body = body.get("custom_data", {})
                request = client.build_request(
                    "POST",
                    url,
                    json=custom_body,
                    headers=headers,
                    params=params,
                )
                await self._log_request(request)

                async with client.stream(
                    "POST",
                    url,
                    json=custom_body,
                    headers=headers,
                    params=params,
                ) as response:
                    logger.info(f"[VersatileProxy] --- Proxy Response (Stream): {response.status_code} ---")
                    logger.debug(f"[VersatileProxy] Response Headers: {dict(response.headers)}")
                    if response.is_error:
                        # 在 stream 上下文内先读出响应体，便于在 raise 前打印错误正文，
                        # 避免上下文退出后 e.response.text 不可读。
                        body_bytes = await response.aread()
                        body_text = body_bytes.decode("utf-8", errors="replace")
                        logger.error(
                            f"[VersatileProxy] HTTP {response.status_code} url={url} "
                            f"body={body_text!r:.500}"
                        )
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        logger.debug(f"[VersatileProxy] proxy received line: {line}]")
                        line = line.strip()
                        if not line:
                            continue
                        # SSE 格式：去掉 "data:" 前缀
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if not line:
                            continue
                        try:
                            outer = _json.loads(line)
                        except Exception:
                            logger.warning(f"[VersatileProxy] 无法解析行：{line!r:.80}")
                            continue

                        yield _unwrap_upstream_frame(outer)

        except httpx.HTTPStatusError as e:
            # 详细错误正文已在 stream 上下文内记录，这里只补一条状态摘要
            logger.error(
                f"[VersatileProxy] HTTPStatusError 已记录："
                f"{e.response.status_code} {url}"
            )
            raise
        except httpx.RequestError as e:
            logger.error(f"[VersatileProxy] 请求错误：{e}")
            raise
