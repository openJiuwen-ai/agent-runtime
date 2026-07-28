"""多运营商消息网关框架。"""

from .core.context import ContextCarrier, GatewayContext, RequestContextFactory
from .lifecycle import (
    InfraContext,
    LifecycleHookRegistry,
    LifecyclePhase,
    MessageContext,
    UserRequest,
)
from .models import MsgType, UnifiedMessage

__all__ = [
    "ContextCarrier",
    "GatewayContext",
    "InfraContext",
    "LifecycleHookRegistry",
    "LifecyclePhase",
    "MessageContext",
    "MsgType",
    "RequestContextFactory",
    "UnifiedMessage",
    "UserRequest",
]
