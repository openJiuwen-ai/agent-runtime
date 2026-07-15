# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileWorkflow — 低码工作流协议适配器。

差异定制：URL 格式化含 params、请求体直传、
帧类型过滤（finish/runCompleted/dialogId 跳过）。
HTTP 流断流由调用方（executor）通过流结束触发 complete()。
"""
from __future__ import annotations

import json as _json
import re
from typing import Optional

from loguru import logger

from adapters.versatile_proxy import VersatileProxy, VersatileStreamCtx
from event.events import (
    AdapterEvent,
    DataProxyContent,
    ExecutionCompletedContent,
    ExecutionInputRequiredContent,
)


class VersatileWorkflow(VersatileProxy):
    """低码工作流协议适配器。"""

    def __init__(
        self,
        url_template: str,
        workflow_id: str,
        timeout: int = 600,
        headers_template: Optional[dict] = None,
        forward_header_whitelist: Optional[set[str]] = None,
        workflow_result_node: Optional[str] = None,
    ) -> None:
        super().__init__(url_template, timeout, headers_template, forward_header_whitelist)
        self._workflow_id = workflow_id
        self._workflow_result_node = workflow_result_node

    def _build_url(self, conv_id: str) -> str:
        return self._url_template.format(conversation_id=conv_id, workflow_id=self._workflow_id)

    def _process_chunk(self, chunk: str, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        if re.search(r'"type"\s*:\s*"(?:finish|runCompleted)"', chunk):
            ctx.completed = True
            return []

        if re.search(r'"event"\s*:\s*"end"', chunk):
            logger.debug(f"[VersatileWorkflow] end 帧，标记完成")
            ctx.completed = True
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

        if re.search(r'"type"\s*:\s*"dialogId"', chunk):
            return []

        if re.search(r'"event"\s*:\s*"(?:error|exception)"', chunk):
            logger.debug(f"[VersatileWorkflow] error/exception 帧，yield data_proxy")
            ctx.completed = True
            ctx.is_failed = True
            ctx.error_message = chunk
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

        if self._workflow_result_node and f'"node_name":"{self._workflow_result_node}"' in chunk:
            try:
                parsed = _json.loads(chunk)
            except Exception:
                logger.warning(f"[VersatileWorkflow] 无法解析 workflow_result 行: {chunk!r:.80}")
                return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]
            data = (parsed.get("custom_rsp_data") or parsed).get("data") or {}
            if isinstance(data, dict) and data.get("node_type") == "QA":
                text = data.get("text", "") or ""
                if not text:
                    return []
                logger.debug(f"[VersatileWorkflow] workflow_result: {text!r:.60}")
                ctx.execution_result = text
                return []

        return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

    def _on_stream_end(self, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        if not ctx.completed:
            return [AdapterEvent(execution_input_required=ExecutionInputRequiredContent())]

        if ctx.is_failed or ctx.execution_result:
            return [AdapterEvent(execution_completed=ExecutionCompletedContent(
                is_failed=ctx.is_failed,
                result=ctx.execution_result or "",
                error_message=ctx.error_message,
            ))]

        return []
