# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import asyncio
from abc import ABC, abstractmethod
from typing import (
    Any,
    Awaitable,
    Callable,
    Optional,
    TYPE_CHECKING,
    AsyncIterator,
    Protocol,
    runtime_checkable,
)

from .models import MessagePriority, MessageType

if TYPE_CHECKING:
    from .models import AccessConfig, SessionConfig


class PriorityMessage(ABC):
    @property
    @abstractmethod
    def priority(self) -> "MessagePriority":
        pass


class RawMessage(PriorityMessage):
    def __init__(
        self,
        message_type: MessageType,
        message: Any,
        priority: MessagePriority = MessagePriority.LOW,
    ) -> None:
        self.message_type = message_type
        self.message = message
        self._priority = priority

    @property
    def priority(self) -> "MessagePriority":
        return self._priority


class IRequest(ABC):
    @property
    @abstractmethod
    def request_id(self) -> Optional[str]:
        pass

    @property
    @abstractmethod
    def chat_id(self) -> Optional[str]:
        pass

    @property
    @abstractmethod
    def user_id(self) -> Optional[str]:
        pass

    @property
    @abstractmethod
    def bot_id(self) -> Optional[str]:
        pass

    @property
    @abstractmethod
    def session_id(self) -> Optional[str]:
        pass


class ISessionRequest(PriorityMessage):
    @property
    @abstractmethod
    def session_id(self) -> str:
        pass

    @property
    @abstractmethod
    def session_concurrency(self) -> int:
        pass

    @property
    @abstractmethod
    def session_ttl(self) -> int:
        pass

    @property
    @abstractmethod
    def request_id(self) -> Optional[str]:
        pass

    @property
    @abstractmethod
    def raw_msg(self) -> Any:
        pass


class ResponseMessage:
    def __init__(self, code: int, message: str, data: dict = None) -> None:
        self.code = code
        self.message = message
        self.data = data


class SessionRequestWrapper:
    def __init__(
        self,
        request: ISessionRequest,
        response_queue: asyncio.Queue[Any],
        cancel: asyncio.Future[Any],
    ) -> None:
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
    def cancel(self) -> asyncio.Future[Any]:
        return self._cancel


class IResponseParser(ABC):
    @abstractmethod
    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        pass

    @abstractmethod
    def is_completed(self, data: dict[str, Any]) -> bool:
        pass

    @abstractmethod
    def response(self, data: dict[str, Any]) -> Any:
        pass


class ITimer(ABC):
    @abstractmethod
    async def start_timer(self, key: str, ttl: int, callback) -> None:
        pass

    @abstractmethod
    async def cancel_timer(self, key: str) -> bool:
        pass


class ISessionStrategy(ABC):
    @abstractmethod
    def handle_session(self, msg: IRequest) -> ISessionRequest:
        pass


class IAccess(ABC):
    @abstractmethod
    async def init(
        self,
        response_parser: IResponseParser,
        strategy: ISessionStrategy,
        config: "AccessConfig",
        session_config: "SessionConfig",
    ) -> None:
        pass

    @abstractmethod
    def send_message(self, msg: IRequest) -> AsyncIterator[Any]:
        pass


@runtime_checkable
class IServiceMessageChannel(Protocol):
    """与下游服务通信；单请求在实例上占 1 服务并发，完成后必须调用 on_request_complete 归还。"""

    async def send(
        self,
        service_id: str,
        wrapper: SessionRequestWrapper,
        *,
        response_parser: IResponseParser,
        on_request_complete: Callable[[Optional[str]], Awaitable[None]],
    ) -> None: ...


class IServiceInstanceFactory(ABC):
    @abstractmethod
    async def new_service(self, response_parser: IResponseParser) -> "IServiceHandler":
        pass


class IServiceManager(ABC):
    @abstractmethod
    async def init(self, response_parser: IResponseParser) -> None:
        pass

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

    @abstractmethod
    async def handle_message(self, msg: "SessionRequestWrapper") -> None:
        pass

    @abstractmethod
    async def enqueue_system(self, event: Any) -> None:
        """投递内部高优先级消息（如缩容、运维事件）。"""


class IServiceHandler(ABC):
    @property
    @abstractmethod
    def id(self) -> str:
        pass

    @property
    @abstractmethod
    def total_concurrency(self) -> int:
        pass

    @property
    @abstractmethod
    def available_concurrency(self) -> int:
        pass

    @property
    @abstractmethod
    def inflight_requests(self) -> int:
        """当前实例上占用的服务级并发（单请求计 1）。"""

    @property
    @abstractmethod
    def active_session_count(self) -> int:
        pass

    @abstractmethod
    def has_session(self, session_id: str) -> bool:
        pass

    @abstractmethod
    async def handle_message(self, msg: "SessionRequestWrapper") -> None:
        pass

    @abstractmethod
    async def remove_session(self, session_id: str) -> int:
        pass

    @abstractmethod
    async def deploy(self) -> None:
        pass

    @abstractmethod
    async def delete(self) -> None:
        pass


class ISessionHandler(ABC):
    @abstractmethod
    async def handle_message(self, msg: "SessionRequestWrapper") -> None:
        pass
