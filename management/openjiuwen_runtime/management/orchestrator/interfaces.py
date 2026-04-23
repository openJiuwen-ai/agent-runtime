# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .models import MessagePriority


class IMessage(ABC):
    """消息接口，提供统一的消息字段访问方法"""

    @abstractmethod
    def get_session_id(self) -> str:
        """获取会话ID"""
        pass

    @abstractmethod
    def get_session_concurrency(self) -> int:
        """获取会话并发度"""
        pass

    @abstractmethod
    def get_session_ttl(self) -> int:
        """获取会话TTL"""
        pass

    @abstractmethod
    def get_request_id(self) -> Optional[str]:
        """获取请求ID"""
        pass

    @abstractmethod
    def get_payload(self) -> Any:
        """获取消息负载"""
        pass

    @abstractmethod
    def get_priority(self) -> "MessagePriority":
        """获取消息优先级"""
        pass

    @abstractmethod
    def is_complete_msg(self) -> bool:
        """是否是最后一个消息"""
        pass

    @abstractmethod
    def get_response_channel(self) -> Optional[Any]:
        """获取响应通道"""
        pass


class Message(BaseModel):
    message_id: str = Field(..., description="消息ID")
    session_id: Optional[str] = Field(None, description="会话ID")
    payload: Any = Field(None, description="消息内容")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")


class ServiceInfo(BaseModel):
    deployment_id: str = Field(..., description="部署ID")
    service_url: str = Field(..., description="服务URL")
    status: str = Field(..., description="服务状态")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")


class IMessageQueue(ABC):
    @abstractmethod
    async def put(self, message: Message) -> None:
        pass

    @abstractmethod
    async def get(self) -> Message:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    @abstractmethod
    async def size(self) -> int:
        pass

    @abstractmethod
    async def is_full(self) -> bool:
        pass


class IServiceManager(ABC):
    @abstractmethod
    async def deploy_service(self) -> str:
        pass

    @abstractmethod
    async def stop_service(self, deployment_id: str) -> bool:
        pass

    @abstractmethod
    async def list_services(self) -> list[ServiceInfo]:
        pass

    @abstractmethod
    async def send_to_service(self, deployment_id: str, message: Message) -> None:
        pass


class ITimer(ABC):
    @abstractmethod
    async def start_timer(self, key: str, ttl: int, callback: Callable) -> None:
        pass

    @abstractmethod
    async def cancel_timer(self, key: str) -> bool:
        pass


class IServiceHandler(ABC):
    @abstractmethod
    async def handle_message(self, message: Message) -> None:
        pass

    @abstractmethod
    async def add_session(self, session_id: str, concurrency: int, ttl: int) -> None:
        pass

    @abstractmethod
    async def remove_session(self, session_id: str) -> None:
        pass

    @abstractmethod
    async def get_session_count(self) -> int:
        pass
