# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""派发结果（设计 §6.3）。

``dispatch`` 返回 ``DispatchResult``：非流式 → ``UnaryResult``（单个 ResponseEnvelope）；
流式 → ``StreamResult``（StreamChunk 异步迭代器）。适配器据此选择响应方式。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Union

from ..envelope import ResponseEnvelope, StreamChunk


@dataclass
class UnaryResult:
    """非流式派发结果。"""

    response: ResponseEnvelope


@dataclass
class StreamResult:
    """流式派发结果：``chunks`` 为 StreamChunk 异步迭代器。"""

    chunks: AsyncIterator[StreamChunk]


DispatchResult = Union[UnaryResult, StreamResult]
