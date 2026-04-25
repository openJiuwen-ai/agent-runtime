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
    TypeAlias,
    runtime_checkable,
)

from .models import MessagePriority, MessageType

if TYPE_CHECKING:
    from .models import AccessConfig, SessionConfig

# 与 ``ServiceHandler`` 传入的 ``async def on_request_complete(r)`` 一致（可 await）
OnRequestCompleteCallback: TypeAlias = Callable[[Optional[str]], Awaitable[None]]


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
    """与下游服务通信；单服务实例上通常是 **一条长连接**（如 WebSocket），上有多路并发流式 ``request_id``。

    **实现方式**：用结构子类型实现本 Protocol（**不要**让具体子类再继承本 Protocol，以免与
    类型检查器对 ``Protocol`` 子类化的限制冲突。）

    约定:
    * ``send`` 中 **上行** 发送一帧业务负载（通常 JSON 序列化自 ``ISessionRequest.raw_msg``）;
    * **下行** 由实现类在独接收循环里按 ``IResponseParser.request_id`` 分片写入对应 ``SessionRequestWrapper.response_queue``;
    * 当某 ``request_id`` 的响应用 ``IResponseParser.is_completed`` 判定结束时，**必须** ``await on_request_complete(request_id)`` 归还本实例并发。
    可选实现(鸭子类型): ``bind_handler(handler, parser)``、``on_pod_ready(service_id, pod_info)``、``close()``.
    """

    async def send(
        self,
        service_id: str,
        wrapper: SessionRequestWrapper,
        *,
        response_parser: IResponseParser,
        on_request_complete: OnRequestCompleteCallback,
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
