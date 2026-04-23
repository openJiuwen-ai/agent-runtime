# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


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
