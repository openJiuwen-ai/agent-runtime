# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Todolist data models."""

from enum import Enum
from typing import List, Optional, Any

from pydantic import BaseModel, ConfigDict, Field


class TodoStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "pending"  # 等待执行
    IN_PROGRESS = "in_progress"  # 执行中
    COMPLETED = "completed"  # 已完成
    CANCELLED = "cancelled"  # 已取消
    FAILED = "failed"  # 执行失败


class TodoItem(BaseModel):
    """
    任务清单单个任务数据模型

    字段说明:
    - index: 任务在线性队列中的位置（从1开始），也是任务唯一标识符
    - content: 任务描述内容
    - status: 任务状态(枚举值)
    - activeForm: 当前执行状态的描述(如: "正在完成任务1")
    - result: 任务执行结果(字符串,可选)

    约束：所有任务依赖线性顺序，仅当 index=1 或前序任务全部 COMPLETED 时才能 start
    """

    index: int = Field(default=1, description="任务在线性队列中的位置（从1开始）")
    content: str = Field(default="", description="任务描述内容")
    status: TodoStatus = Field(default=TodoStatus.PENDING, description="任务状态")
    activeForm: str = Field(default="", description="当前执行状态的描述")
    result: Optional[str] = Field(default=None, description="任务执行结果")

    model_config = ConfigDict(use_enum_values=True)


class ToolOutput(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


# 类型别名
TodoList = List[TodoItem]

__all__ = [
    "TodoStatus",
    "TodoItem",
    "TodoList",
    "ToolOutput",
]
