# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import asyncio
from abc import ABC, abstractmethod
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
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

    @property
    @abstractmethod
    def service_template(self) -> Optional[Dict[str, Any]]:
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

    async def stop_all(self) -> None:
        """若实现支持，取消所有活动计时；默认无操作。"""
        return


class ISessionStrategy(ABC):
    @abstractmethod
    def handle_session(self, msg: IRequest) -> ISessionRequest:
        pass

    @abstractmethod
    def configure(self, concurrency: int, ttl: int) -> None:
        """配置会话的并发度和 TTL"""
        pass


class IAccess(ABC):
    @abstractmethod
    async def init(
            self,
            response_parser: IResponseParser,
            strategy: ISessionStrategy,
            config: "AccessConfig",
            session_config: "SessionConfig",
            service_configs: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        pass

    @abstractmethod
    def send_message(self, msg: IRequest) -> AsyncIterator[Any]:
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """优雅退出：停后台 task/双队列/计时器，并释放已拉起的各服务实例（如 Pod/连接）。"""
        pass

    @abstractmethod
    async def update_config(
            self, config: Optional["AccessConfig"] = None, session_config: Optional["SessionConfig"] = None,
            strategy: Optional["ISessionStrategy"] = None,
    ) -> None:
        """运行时热更新配置。存量 session/service 不变，新建的使用新值。
        
        Args:
            config: 新的 AccessConfig 配置。如果为 None 或默认空配置，则不更新 config。
            session_config: 可选的会话配置。
            strategy: 可选的会话策略。
        """
        pass

    @abstractmethod
    async def cleanup_all_pods(self, namespace: str, *, kubeconfig: Optional[str] = None,
                               label_selector: str = "") -> None:
        """通过 K8s label selector 清理所有 AgentServer Pod。

        典型场景：gateway 主备切换，新 PRIMARY 清理旧 PRIMARY 遗留的 Pod。
        """
        pass


@runtime_checkable
class IServiceMessageChannel(Protocol):
    """与下游服务通信；单服务实例上通常是 **一条长连接**（如 WebSocket），上有多路并发流式 ``request_id``。

    **实现方式**：用结构子类型实现本 Protocol（**不要**让具体子类再继承本 Protocol，以免与
    类型检查器对 ``Protocol`` 子类化的限制冲突。）

    约定:
    * ``send`` 中 **上行** 发送一帧业务负载（通常 JSON 序列化自 ``ISessionRequest.raw_msg``）;
    * **下行** 由实现类在独接收循环里按 ``IResponseParser.request_id`` 分片写入对应 ``SessionRequestWrapper.response_queue``;
    * 当某 ``request_id`` 的响应用 ``IResponseParser.is_completed`` 判定结束时，**必须**
    ``await on_request_complete(request_id)`` 归还本实例并发。
    可选实现(鸭子类型): ``bind_handler(handler, parser)``、``on_pod_ready(service_id, pod_info)``、``close()``.
    """

    async def send(self, service_id: str, wrapper: SessionRequestWrapper, *, response_parser: IResponseParser,
                   on_request_complete: OnRequestCompleteCallback,
                   ) -> None:
        pass


@runtime_checkable
class ISendEndpoint(Protocol):
    """SessionHandler 通过此接口向某个 Pod 发送请求，不绑定具体 ServiceHandler 类型。

    约定:
    * 由 ``ServiceHandler`` 实现（结构子类型，不要让具体子类再继承本 Protocol）；
    * ``send_message`` 完成 inflight 计数、request_id → wrapper 映射（供下行分片）、
      实际的 ``IServiceMessageChannel.send`` 以及完成回调（inflight 归零通知 idle_pool_hook）；
    * ``inflight`` / ``endpoint_id`` 用于 SessionHandler 的最少负载路由与用户亲和记录。

    单 Pod 场景：SessionHandler 持有 1 个端点；多 Pod 场景：持有 N 个端点。
    """

    @property
    def endpoint_id(self) -> str:
        """端点唯一标识（ServiceHandler 场景下等于 service_id）。"""
        ...

    @property
    def inflight(self) -> int:
        """当前端点在途请求数，用于最少负载路由。"""
        ...

    async def send_message(self, wrapper: SessionRequestWrapper) -> None:
        """发送请求并追踪完成。SessionHandler 在获取信号量后调用此方法。"""
        ...


class IServiceInstanceFactory(ABC):
    @abstractmethod
    async def new_service(
        self, response_parser: IResponseParser, service_template: Optional[Dict[str, Any]] = None
    ) -> "IServiceHandler":
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

    @abstractmethod
    def mark_deprecated(self) -> None:
        """标记当前 ServiceManager 为待老化状态。调用 update_config 时自动调用。"""

    @abstractmethod
    def is_deprecated(self) -> bool:
        """检查当前 ServiceManager 是否处于待老化状态。"""

    @abstractmethod
    async def try_cleanup_if_idle(self) -> bool:
        """尝试清理当前 ServiceManager（如果无在途任务和活跃 session）。
        
        Returns:
            True 表示已清理成功，False 表示仍有活跃任务无法清理。
        """


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

    @abstractmethod
    def try_reserve_session_quota(self, session_id: str, quota: int) -> bool:
        """为新 session 预留 ``quota`` 点服务并发；同一 session 重复调用须幂等成功。"""

    @property
    @abstractmethod
    def inflight_requests(self) -> int:
        """当前实例上通道在途请求数（消息粒度），用于 TTL / idle；不等同于服务额度占用。"""

    @property
    @abstractmethod
    def active_session_count(self) -> int:
        """当前实例上仍预留额度的 session 数（基于 ``_session_reserved``）。

        解耦后语义为 quota 计数，用于 idle/TTL 判定，而非 SessionHandler 实例数。
        """
        pass

    def open_session_ids(self) -> list[str]:
        """当前实例上仍预留额度的 session_id 列表；默认无。转入 idle 前可据此清理亲和路由。"""
        return []

    @abstractmethod
    async def evict_session(self, session_id: str) -> int:
        """释放该 session 在本实例的预留额度，并取消本实例上该 session 的在途请求（pod-local）。

        解耦后取代原 ``remove_session``：只处理 pod-local 状态（quota + 取消），
        不销毁 SessionHandler 实例（后者由 SessionRegistry 负责）。
        """
        pass

    @abstractmethod
    async def deploy(self) -> None:
        pass

    @property
    @abstractmethod
    def service_ttl(self) -> Optional[int]:
        """服务实例的 idle TTL（秒）。从 service_template 提取，可能为 None（表示使用 Manager 的默认值）。"""
        pass

    @abstractmethod
    async def delete(self) -> None:
        pass

    def set_idle_pool_transition_hook(
            self, hook: Optional[Callable[[str], Awaitable[None]]]
    ) -> None:
        """默认无实现；`ServiceHandler` 会注入，供无业务时按 `service_ttl` 转入 idle 池。"""
        return


class ISessionHandler(ABC):
    @abstractmethod
    async def handle_message(self, msg: "SessionRequestWrapper") -> None:
        pass
