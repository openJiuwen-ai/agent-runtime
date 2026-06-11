# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileWorkflow — 低码工作流协议适配器。

差异定制：URL 格式化含 params、请求体直传、
帧类型过滤（finish/runCompleted/dialogId 跳过）。
HTTP 流断流由调用方（executor）通过流结束触发 complete()。
"""
from __future__ import annotations

import re as _re
from typing import Optional

from adapters.versatile_proxy import VersatileProxy, VersatileStreamCtx
from event.events import (
    AdapterEvent,
    DataProxyContent,
)


class VersatileWorkflow(VersatileProxy):
    """低码工作流协议适配器。"""

    _SKIP_TYPE_PATTERN = _re.compile(
        r'"type"\s*:\s*"(?:finish|runCompleted|dialogId)"'
    )

    def __init__(
        self,
        url_template: str,
        workflow_id: str,
        timeout: int = 600,
        headers_template: Optional[dict] = None,
        forward_header_whitelist: Optional[set[str]] = None,
    ) -> None:
        super().__init__(url_template, timeout, headers_template, forward_header_whitelist)
        self._workflow_id = workflow_id

    def _build_url(self, conv_id: str) -> str:
        return self._url_template.format(conversation_id=conv_id, workflow_id=self._workflow_id)

    def _process_chunk(self, chunk: str, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        if self._SKIP_TYPE_PATTERN.search(chunk):
            return []
        return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

    def _on_stream_end(self, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        return []
