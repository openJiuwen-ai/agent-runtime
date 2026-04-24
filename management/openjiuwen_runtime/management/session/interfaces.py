# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from .models import MessagePriority


class PriorityMessage(ABC):
    @property
    @abstractmethod
    def priority(self) -> "MessagePriority":
        """获取消息优先级"""
        pass


class IRequest(ABC):
    """入口用户消息"""

    @property
    @abstractmethod
    def request_id(self) -> Optional[str]:
        """获取请求唯一ID"""
        pass

    @property
    @abstractmethod
    def chat_id(self) -> Optional[str]:
        """群ID"""
        pass

    @property
    @abstractmethod
    def user_id(self) -> Optional[str]:
        """用户ID"""
        pass

    @property
    @abstractmethod
    def bot_id(self) -> Optional[str]:
        """botID"""
        pass

    @property
    @abstractmethod
    def session_id(self) -> Optional[str]:
        """会话ID"""
        pass


class ISessionRequest(PriorityMessage):
    """session级消息接口，提供统一的消息字段访问方法"""

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
    def raw_msg(self) -> Any:
        """获取原始消息用于发送"""
        pass


class SessionRequestWrapper:
    def __init__(self, request: ISessionRequest, response_queue: asyncio.Queue[Any], cancel: asyncio.Future):
        self._session_request = request
        self._response_queue = response_queue
        self._cancel = cancel

    @property
    def session_request(self) -> ISessionRequest:
        return self._session_request

    @property
    def response_queue(self) -> asyncio.Queue[Any]:
        return self._response_queue

    @property
    def cancel(self) -> asyncio.Future:
        return self._cancel


class IResponseParser(ABC):
    """响应消息解析器，根据响应chunk解析得到request id和结束字段"""

    @abstractmethod
    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        """获取请求唯一ID"""
        pass

    @abstractmethod
    def is_completed(self, data: dict[str, Any]) -> bool:
        """是否执行完成"""
        pass

    @abstractmethod
    def response(self, data: dict[str, Any]) -> Any:
        """返回需要格式的响应"""
        pass


class IRequestQueue(ABC):
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


class ITimer(ABC):
    @abstractmethod
    async def start_timer(self, key: str, ttl: int, callback: Callable) -> None:
        pass

    @abstractmethod
    async def cancel_timer(self, key: str) -> bool:
        pass


class ISessionStrategy(ABC):
    """session映射策略"""

    @abstractmethod
    def handle_session(self, msg: IRequest) -> ISessionRequest:
        pass


class IAccess(ABC):
    """消息统一入口，内部调用session策略，把IRequest转换为ISessionRequest"""

    @abstractmethod
    def init(self, response_parser: IResponseParser, strategy: ISessionStrategy):
        pass

    @abstractmethod
    def send_message(self, msg: IRequest) -> AsyncIterator[Any]:
        pass


class IServiceManager(ABC):
    """服务管理"""

    @abstractmethod
    def init(self, response_parser: IResponseParser):
        pass

    @abstractmethod
    def handle_message(self, msg: SessionRequestWrapper):
        pass


class IServiceHandler(ABC):
    """服务入口"""

    @abstractmethod
    async def handle_message(self, msg: SessionRequestWrapper):
        pass

    async def deploy(self):
        pass

    async def delete(self):
        pass


class ISessionHandler(ABC):
    @abstractmethod
    def handle_message(self, msg: SessionRequestWrapper):
        pass
