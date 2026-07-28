from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from ..core.context import ContextCarrier
from ..models import UnifiedMessage


class LifecyclePhase(str, Enum):
    GATEWAY_START = "gateway_start"
    GATEWAY_STOP = "gateway_stop"
    FEATURE_START = "feature_start"
    FEATURE_STOP = "feature_stop"
    CHANNEL_REGISTER = "channel_register"
    CHANNEL_UNREGISTER = "channel_unregister"
    CHANNEL_CONNECT = "channel_connect"
    CHANNEL_DISCONNECT = "channel_disconnect"
    MESSAGE_RECV = "message_recv"
    MESSAGE_NORMALIZED = "message_normalized"
    MIDDLEWARE_BEFORE = "middleware_before"
    MIDDLEWARE_AFTER = "middleware_after"
    ROUTE_BEFORE = "route_before"
    ROUTE_AFTER = "route_after"
    HANDLER_BEFORE = "handler_before"
    HANDLER_AFTER = "handler_after"
    HANDLER_ERROR = "handler_error"
    HANDLER_SKIP = "handler_skip"
    ROUTE_MATCHED = "route_matched"
    ROUTE_NO_HANDLER = "route_no_handler"
    EXECUTOR_CONNECT = "executor_connect"
    EXECUTOR_DISCONNECT = "executor_disconnect"
    EXECUTOR_PUSH = "executor_push"
    EXECUTOR_BEFORE = "executor_before"
    EXECUTOR_AFTER = "executor_after"
    MESSAGE_SEND_BEFORE = "message_send_before"
    MESSAGE_SEND = "message_send"
    MESSAGE_SEND_AFTER = "message_send_after"
    MESSAGE_SEND_ERROR = "message_send_error"


INFRA_PHASES: frozenset[LifecyclePhase] = frozenset(
    {
        LifecyclePhase.GATEWAY_START,
        LifecyclePhase.GATEWAY_STOP,
        LifecyclePhase.FEATURE_START,
        LifecyclePhase.FEATURE_STOP,
        LifecyclePhase.CHANNEL_REGISTER,
        LifecyclePhase.CHANNEL_UNREGISTER,
        LifecyclePhase.CHANNEL_CONNECT,
        LifecyclePhase.CHANNEL_DISCONNECT,
    }
)
MESSAGE_PHASES: frozenset[LifecyclePhase] = frozenset(
    p for p in LifecyclePhase if p not in INFRA_PHASES
)


@dataclass
class InfraContext:
    trace_id: str
    phase: LifecyclePhase
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    span_id: str = field(default_factory=lambda: uuid4().hex[:16])
    channel_name: str | None = None
    carrier: str | None = None
    feature_name: str | None = None
    error: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    context_carrier: ContextCarrier | None = None

    def set_attr(self, key: str, value: Any) -> "InfraContext":
        self.attrs[key] = value
        return self


@dataclass
class MessageContext:
    trace_id: str
    phase: LifecyclePhase
    msg_id: str
    msg_type: str
    carrier: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    span_id: str = field(default_factory=lambda: uuid4().hex[:16])
    channel_name: str | None = None
    handler_name: str | None = None
    error: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    context_carrier: ContextCarrier | None = None

    def set_attr(self, key: str, value: Any) -> "MessageContext":
        self.attrs[key] = value
        return self

    def fork(self, phase: LifecyclePhase, **patch: Any) -> "MessageContext":
        return MessageContext(
            trace_id=self.trace_id,
            phase=phase,
            msg_id=self.msg_id,
            msg_type=self.msg_type,
            carrier=self.carrier,
            channel_name=patch.get("channel_name", self.channel_name),
            handler_name=patch.get("handler_name", self.handler_name),
            error=patch.get("error", self.error),
            context_carrier=patch.get("context_carrier", self.context_carrier),
            attrs=dict(self.attrs),
        )


@dataclass
class UserRequest:
    message: UnifiedMessage | None = None
    raw: Any = None
    handler_name: str | None = None
    executor_payload: dict[str, Any] | None = None
    executor_result: Any = None
    extra: dict[str, Any] = field(default_factory=dict)
    context_carrier: ContextCarrier | None = None
