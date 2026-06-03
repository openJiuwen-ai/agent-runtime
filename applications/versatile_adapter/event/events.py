# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
AdapterEvent — VersatileAdapterRunner 输出的标准化事件类型（discriminated union）。

设计原则：
  - 不为远端 SSE 流的每种格式定义事件类型
  - 绝大部分通过 DataProxyContent 直接透传
  - 仅对需特殊处理的节点定义专属类型
  - 同一时刻仅一个内容字段非 None，通过 event_type() 判别
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DataProxyContent(BaseModel, frozen=True):
    """原始后端帧数据原样透传。"""
    raw_data: str


class ExecutionInputRequiredContent(BaseModel, frozen=True):
    """非终态信号：需要前端继续输入以推进下一轮。"""
    pass


class ExecutionCompletedContent(BaseModel, frozen=True):
    """终态信号：任务完成并携带工作流结果。"""
    is_failed: bool = False
    result: str


class AdapterEvent(BaseModel):
    """Runner 输出的标准化事件（discriminated union）。

    同一时刻仅一个内容字段非 None。
    """
    data_proxy: Optional[DataProxyContent] = Field(
        default=None, description="原始后端帧数据原样透传。"
    )
    execution_input_required: Optional[ExecutionInputRequiredContent] = Field(
        default=None, description="需要前端继续输入以推进下一轮。"
    )
    execution_completed: Optional[ExecutionCompletedContent] = Field(
        default=None, description="任务完成并携带工作流结果。"
    )
