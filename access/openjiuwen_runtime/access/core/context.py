"""全链路运行时上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import UnifiedMessage


@dataclass(slots=True)
class ContextCarrier:
    """Access 传播的不透明业务上下文载体。"""

    global_context: object | None = None
    request_context: object | None = None

    def current(self) -> object | None:
        if self.request_context is not None:
            return self.request_context
        return self.global_context


@dataclass
class GatewayContext:
    """贯穿 inject → middleware → router → handler → send 的请求上下文。"""

    trace_id: str
    session_id: str | None = None
    tenant_id: str | None = None
    carrier: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    context_carrier: ContextCarrier | None = None

    def set_attr(self, key: str, value: Any) -> GatewayContext:
        self.attrs[key] = value
        return self

    def fork(self, **patch: Any) -> GatewayContext:
        attrs_patch = patch.get("attrs", {})
        if not isinstance(attrs_patch, dict):
            raise TypeError(f"attrs must be a dict, got {type(attrs_patch).__name__}")

        return GatewayContext(
            trace_id=patch.get("trace_id", self.trace_id),
            session_id=patch.get("session_id", self.session_id),
            tenant_id=patch.get("tenant_id", self.tenant_id),
            carrier=patch.get("carrier", self.carrier),
            context_carrier=patch.get("context_carrier", self.context_carrier),
            attrs={**self.attrs, **attrs_patch},
        )

    @classmethod
    def from_message(cls, msg: Any, *, trace_id: str) -> GatewayContext:
        payload = getattr(msg, "payload", None) or {}
        session_id = None
        if isinstance(payload, dict):
            session_id = payload.get("session_id") or payload.get("session")
        dst = getattr(msg, "dst", None)
        if not session_id and isinstance(dst, str):
            session_id = dst
        return cls(
            trace_id=trace_id,
            session_id=str(session_id) if session_id else None,
            carrier=str(getattr(msg, "carrier", "") or "") or None,
        )


class RequestContextFactory(Protocol):
    """构造并补充应用自有请求上下文的同步工厂接口。"""

    def global_context(self) -> object | None: ...

    def create_request_context(
        self,
        *,
        message: UnifiedMessage,
        gateway_context: GatewayContext,
    ) -> object | None: ...

    def enrich_normalized_context(
        self,
        context: object,
        *,
        message: UnifiedMessage,
        gateway_context: GatewayContext,
    ) -> None: ...
