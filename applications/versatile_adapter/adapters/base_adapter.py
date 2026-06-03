# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
BaseAdapter — 后端协议适配器抽象基类。

每种后端类型实现此接口，封装 HTTP 交互 + SSE 解析 + 报文转换 + 节点处理。
直接 yield AdapterEvent，不依赖 A2A SDK。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional

from event.events import AdapterEvent


class BaseAdapter(ABC):
    """后端协议适配器抽象基类。"""

    @abstractmethod
    async def dispatch_stream(
        self,
        conv_id: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> AsyncGenerator[AdapterEvent, None]:
        """向后端发起流式请求，yield 类型化的 AdapterEvent。

        Args:
            conv_id: 会话 ID，供 _build_url 格式化。
            headers: 每个 HTTP 请求的输入头。
            params: URL 查询参数。
            body: 业务负载。
        """
        ...
