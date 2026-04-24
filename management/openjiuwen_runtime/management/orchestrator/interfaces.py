# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .models import MessagePriority, Message


class PriorityMessage(ABC):
    @property
    @abstractmethod
    def priority(self) -> "MessagePriority":
        """获取消息优先级"""
        pass


class IMessage(PriorityMessage):
    """消息接口，提供统一的消息字段访问方法"""

    @property
    @abstractmethod
    def session_id(self) -> str:
        """获取会话ID"""
        pass

    @property
    @abstractmethod
    def session_concurrency(self) -> int:
        """获取会话并发度"""
        pass

    @property
    @abstractmethod
    def session_ttl(self) -> int:
        """获取会话TTL"""
        pass

    @property
    @abstractmethod
    def request_id(self) -> Optional[str]:
        """获取请求唯一ID"""
        pass

    @property
    @abstractmethod
    def payload(self) -> Any:
        """获取消息负载"""
        pass


class MessageWrapper(IMessage):

    def __init__(self, message: IMessage, queue: asyncio.Queue):
        self._message = message
        self._queue = queue

    @property
    def message(self) -> IMessage:
        return self._message

    @property
    def queue(self) -> asyncio.Queue:
        """获取响应通道"""
        return self._queue

    @property
    def session_id(self) -> str:
        return self._message.session_id

    @property
    def session_concurrency(self) -> int:
        return self._message.session_concurrency

    @property
    def session_ttl(self) -> int:
        return self._message.session_ttl

    @property
    def request_id(self) -> Optional[str]:
        return self._message.request_id

    @property
    def payload(self) -> Any:
        return self._message.payload

    @property
    def priority(self) -> "MessagePriority":
        return self._message.priority


class ServiceInfo(BaseModel):
    deployment_id: str = Field(..., description="部署ID")
    service_url: str = Field(..., description="服务URL")
    status: str = Field(..., description="服务状态")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")


class IMessageQueue(ABC):
    @abstractmethod
    async def put(self, message: PriorityMessage) -> None:
        pass

    @abstractmethod
    async def get(self) -> PriorityMessage:
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
    async def send_to_service(self, deployment_id: str, message: "Message") -> None:
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
    async def handle_message(self, message: "Message") -> None:
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

    @abstractmethod
    async def deploy(self) -> bool:
        """部署服务"""
        pass

    @abstractmethod
    async def undeploy(self) -> bool:
        """卸载服务"""
        pass

    @abstractmethod
    async def start(self) -> None:
        """启动事件循环"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止事件循环"""
        pass
