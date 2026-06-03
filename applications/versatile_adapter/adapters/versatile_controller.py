# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileController — 一级控制器协议适配器。

差异定制：custom_data 请求体提取、
End/exception 终态检测、GXZQAResponseNode 过滤。
"""
from __future__ import annotations

import json as _json
from typing import Optional

from loguru import logger

from adapters.versatile_proxy import VersatileProxy, VersatileStreamCtx
from event.events import (
    AdapterEvent,
    DataProxyContent,
    ExecutionCompletedContent,
    ExecutionInputRequiredContent,
)


_FORWARD_HEADER_WHITELIST = (
    "x-user-id", "x-project-id", "cust-token", "cust-userid",
    # 允许上游动态覆盖模板里的 Cookie（用户级 AGENT_SID 等）
    "cookie",
)


class VersatileController(VersatileProxy):
    """一级控制器协议适配器。"""

    def __init__(
        self,
        url_template: str,
        timeout: int = 600,
        headers_template: Optional[dict] = None,
        forward_header_whitelist: Optional[set[str]] = None,
        workflow_result_node: Optional[str] = None,
    ) -> None:
        header_whitelist = forward_header_whitelist if forward_header_whitelist else _FORWARD_HEADER_WHITELIST
        super().__init__(url_template, timeout, headers_template, header_whitelist)
        self._workflow_result_node = workflow_result_node

    def _process_chunk(self, chunk: str, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        if '"node_type":"End"' in chunk:
            logger.debug(f"[VersatileController] End 节点，yield data_proxy")
            ctx.completed = True
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

        if '"event":"exception"' in chunk:
            logger.debug(f"[VersatileController] exception 帧，yield data_proxy")
            ctx.completed = True
            ctx.is_failed = True
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

        if self._workflow_result_node and f'"node_name":"{self._workflow_result_node}"' in chunk:
            try:
                parsed = _json.loads(chunk)
            except Exception:
                logger.warning(f"[VersatileController] 无法解析 workflow_result 行: {chunk!r:.80}")
                return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]
            data = (parsed.get("custom_rsp_data") or parsed).get("data") or {}
            if isinstance(data, dict) and data.get("node_type") == "QA":
                text = data.get("text", "") or ""
                if not text:
                    return []
                logger.debug(f"[VersatileController] workflow_result: {text!r:.60}")
                ctx.execution_result = text
                return []

        return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

    def _on_stream_end(self, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        if not ctx.completed:
            return [AdapterEvent(execution_input_required=ExecutionInputRequiredContent())]

        if ctx.execution_result:
            return [AdapterEvent(execution_completed=ExecutionCompletedContent(
                is_failed=ctx.is_failed, result=ctx.execution_result
            ))]

        return []
