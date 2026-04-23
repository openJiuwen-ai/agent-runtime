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


def wrap_versatile_response(
    outer: dict,
    conv_id: str,
    agent_id: Optional[str] = None
) -> dict:
    """包装 Versatile 返回数据（包装整个 outer）"""
    return {
        "success": True,
        "agent_id": agent_id or "",
        "conversation_id": conv_id,
        "custom_rsp_data": outer
    }


class VersatileProxy:
    def __init__(self, url_template: str, timeout: int = 600) -> None:
        self._url_template = url_template
        self._timeout = timeout

    def _build_url(self, conv_id: str) -> str:
        return self._url_template.format(conversation_id=conv_id)

    def _generate_curl_command(self, request: httpx.Request, body: bytes) -> str:
        """生成 curl 命令用于调试"""
        cmd = f"curl -X {request.method} '{request.url}'"
        for key, value in request.headers.items():
            cmd += f" -H '{key}: {value}'"
        if body:
            try:
                json_body = _json.loads(body.decode('utf-8'))
                cmd += f" -d '{_json.dumps(json_body, ensure_ascii=False)}'"
            except Exception:
                cmd += f" -d '{body.decode('utf-8', errors='replace')}'"
        return cmd

    async def _log_request(self, request: httpx.Request) -> None:
        """记录请求日志（生成 curl 命令）"""
        body = await request.aread()
        curl = self._generate_curl_command(request, body)
        logger.info(f"\n{'='*20} Proxy Request (Stream) Start {'='*20}\n{curl}\n{'='*20} Proxy Request (Stream) End {'='*20}")

    async def dispatch_stream(
        self,
        body: dict,
        conv_id: str,
        extra_headers: Optional[dict] = None,
        params: Optional[dict] = None,
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
        logger.debug(f"[VersatileProxy] 请求参数：{params})")

        try:
            async with httpx.AsyncClient(
                verify=False,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                timeout=httpx.Timeout(self._timeout, read=None),
            ) as client:
                # 构建请求对象用于日志
                request = client.build_request("POST", url, json=body.get("custom_data", {}), headers=headers, params=params)
                await self._log_request(request)
                
                async with client.stream("POST", url, json=body.get("custom_data", {}), headers=headers, params=params) as response:
                    logger.info(f"[VersatileProxy] --- Proxy Response (Stream): {response.status_code} ---")
                    logger.debug(f"[VersatileProxy] Response Headers: {dict(response.headers)}")
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        logger.info(f"[VersatileProxy] proxy received line: {line}]")
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
                      
                        # 从 body 中提取 agent_id
                        agent_id = body.get("agent_id", "")
                        
                        # 包装整个 outer
                        wrapped_chunk = wrap_versatile_response(
                            outer=outer,
                            conv_id=conv_id,
                            agent_id=agent_id
                        )
                        
                        yield wrapped_chunk

        except httpx.HTTPStatusError as e:
            logger.error(f"[VersatileProxy] HTTP 错误：{e.response.status_code} {url}")
        except httpx.RequestError as e:
            logger.error(f"[VersatileProxy] 请求错误：{e}")
