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
}


class VersatileProxy:
    def __init__(self, url_template: str, timeout: int = 600) -> None:
        self._url_template = url_template
        self._timeout = timeout

    def _build_url(self, conv_id: str) -> str:
        return self._url_template.format(conversation_id=conv_id)

    async def dispatch_stream(
        self,
        body: dict,
        conv_id: str,
        extra_headers: Optional[dict] = None,
    ) -> AsyncGenerator[dict, None]:
        url = self._build_url(conv_id)
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(
                {k: v for k, v in extra_headers.items() if k.lower() in _FORWARD_HEADER_WHITELIST}
            )

        logger.info(f"[VersatileProxy] 发送请求：POST {url}")
        logger.debug(f"[VersatileProxy] 请求头：{headers}")
        logger.debug(f"[VersatileProxy] 请求体：{body.get('custom_data', {})})")

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", url, json=body.get("custom_data", {}), headers=headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
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

                        # # 外层结构：{"event": "message"/"end", "data": {...}}
                        # event_type = outer.get("event")
                        # if event_type == "end":
                        #     # 流结束信号，无数据，跳过
                        #     continue
                        chunk = outer.get("data")
                        if not isinstance(chunk, dict) or not chunk:
                            continue

                        logger.debug(f"[VersatileProxy] proxy received chunk: {chunk}]")
                        yield chunk

        except httpx.HTTPStatusError as e:
            logger.error(f"[VersatileProxy] HTTP 错误：{e.response.status_code} {url}")
        except httpx.RequestError as e:
            logger.error(f"[VersatileProxy] 请求错误：{e}")
