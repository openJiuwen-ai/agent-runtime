# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Orchestrator 数据模型"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .interfaces import IMessage


class MessagePriority(str, Enum):
    """消息优先级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ServiceState(str, Enum):
    """服务状态"""
    RESERVED = "reserved"
    IDLE = "idle"
    RUNNING = "running"


class SessionState(str, Enum):
    """会话状态"""
    IDLE = "idle"
    RUNNING = "running"


class Message(BaseModel, IMessage):
    """消息模型"""
    session_id: str = Field(..., description="会话ID")
    request_id: Optional[str] = Field(None, description="请求ID")
    concurrency: int = Field(..., description="并发数")
    ttl: int = Field(..., description="生存时间（秒）")
    priority: MessagePriority = Field(..., description="消息优先级")
    payload: Any = Field(..., description="消息负载")
    response_channel: Optional[Any] = Field(None, description="响应通道")
    is_complete: bool = Field(False, description="是否是最后一个消息")

    def get_session_id(self) -> str:
        return self.session_id

    def get_session_concurrency(self) -> int:
        return self.concurrency

    def get_session_ttl(self) -> int:
        return self.ttl

    def get_request_id(self) -> Optional[str]:
        return self.request_id

    def get_payload(self) -> Any:
        return self.payload

    def get_priority(self) -> MessagePriority:
        return self.priority

    def is_complete_msg(self) -> bool:
        return self.is_complete


class SessionInfo(BaseModel):
    """会话信息模型"""
    session_id: str = Field(..., description="会话ID")
    concurrency: int = Field(..., description="并发数")
    ttl: int = Field(..., description="生存时间（秒）")
    state: SessionState = Field(..., description="会话状态")
    created_at: float = Field(..., description="创建时间戳")
    last_active_at: float = Field(..., description="最后活跃时间戳")
    pending_requests: dict[str, Any] = Field(default_factory=dict, description="已发送请求映射，key是request_id，value是response_channel")


class ServiceInfo(BaseModel):
    """服务信息模型"""
    deployment_id: str = Field(..., description="部署ID")
    state: ServiceState = Field(..., description="服务状态")
    sessions: dict[str, SessionInfo] = Field(default_factory=dict, description="会话映射")
    created_at: float = Field(..., description="创建时间戳")
