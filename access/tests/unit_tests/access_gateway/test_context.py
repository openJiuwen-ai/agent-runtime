from dataclasses import dataclass, field

import pytest

from openjiuwen_runtime import access
from openjiuwen_runtime.access.core.context import (
    ContextCarrier,
    GatewayContext,
    RequestContextFactory,
)
from openjiuwen_runtime.access.models import MsgType, UnifiedMessage


@dataclass
class _RequestContext:
    message_id: str
    attrs: dict[str, object] = field(default_factory=dict)


class _ContextFactory:
    def __init__(self, global_context: object) -> None:
        self._global_context = global_context

    def global_context(self) -> object:
        return self._global_context

    def create_request_context(
        self,
        *,
        message: UnifiedMessage,
        gateway_context: GatewayContext,
    ) -> object:
        return _RequestContext(message_id=message.msg_id)

    def enrich_normalized_context(
        self,
        context: object,
        *,
        message: UnifiedMessage,
        gateway_context: GatewayContext,
    ) -> None:
        assert isinstance(context, _RequestContext)
        context.attrs["trace_id"] = gateway_context.trace_id


def _message(
    *,
    payload: dict[str, object] | None = None,
    dst: str = "destination-session",
) -> UnifiedMessage:
    return UnifiedMessage(
        msg_id="message-1",
        msg_type=MsgType.USER_REQUEST,
        carrier="web",
        src="user-1",
        dst=dst,
        payload=payload or {},
    )


@pytest.mark.parametrize(
    ("carrier", "expected"),
    [
        (ContextCarrier(), None),
        (ContextCarrier(global_context="global"), "global"),
        (
            ContextCarrier(
                global_context="global",
                request_context="request",
            ),
            "request",
        ),
    ],
)
def test_context_carrier_current_selection(
    carrier: ContextCarrier,
    expected: object | None,
) -> None:
    assert carrier.current() == expected


def test_gateway_context_set_attr_returns_same_context() -> None:
    context = GatewayContext(trace_id="trace-1")

    result = context.set_attr("stage", "received")

    assert result is context
    assert context.attrs == {"stage": "received"}


def test_gateway_context_fork_copies_fields_and_applies_overrides() -> None:
    context_carrier = ContextCarrier(global_context=object())
    context = GatewayContext(
        trace_id="trace-1",
        session_id="session-1",
        tenant_id="tenant-1",
        carrier="web",
        attrs={"shared": "original", "retained": True},
        context_carrier=context_carrier,
    )

    child = context.fork(
        trace_id="trace-2",
        session_id=None,
        attrs={"shared": "child", "added": True},
    )

    assert child is not context
    assert child.trace_id == "trace-2"
    assert child.session_id is None
    assert child.tenant_id == "tenant-1"
    assert child.carrier == "web"
    assert child.context_carrier is context_carrier
    assert child.attrs == {
        "shared": "child",
        "retained": True,
        "added": True,
    }

    child.set_attr("child-only", True)
    assert context.attrs == {"shared": "original", "retained": True}


@pytest.mark.parametrize("attrs", [None, [], "invalid"])
def test_gateway_context_fork_rejects_non_dict_attrs(attrs: object) -> None:
    context = GatewayContext(trace_id="trace-1", attrs={"retained": True})

    with pytest.raises(TypeError, match=r"^attrs must be a dict, got "):
        context.fork(attrs=attrs)

    assert context.attrs == {"retained": True}


@pytest.mark.parametrize(
    ("payload", "dst", "expected_session_id"),
    [
        ({"session_id": "payload-id", "session": "fallback"}, "dst", "payload-id"),
        ({"session": "payload-session"}, "dst", "payload-session"),
        ({}, "destination-session", "destination-session"),
        ({}, "", None),
    ],
)
def test_gateway_context_from_message_extracts_session_id(
    payload: dict[str, object],
    dst: str,
    expected_session_id: str | None,
) -> None:
    context = GatewayContext.from_message(
        _message(payload=payload, dst=dst),
        trace_id="trace-1",
    )

    assert context.trace_id == "trace-1"
    assert context.session_id == expected_session_id
    assert context.carrier == "web"


def test_request_and_global_context_objects_are_preserved() -> None:
    global_context = object()
    factory: RequestContextFactory = _ContextFactory(global_context)
    message = _message()
    gateway_context = GatewayContext.from_message(message, trace_id="trace-1")

    request_context = factory.create_request_context(
        message=message,
        gateway_context=gateway_context,
    )
    assert isinstance(request_context, _RequestContext)
    factory.enrich_normalized_context(
        request_context,
        message=message,
        gateway_context=gateway_context,
    )
    context_carrier = ContextCarrier(
        global_context=factory.global_context(),
        request_context=request_context,
    )
    gateway_context.context_carrier = context_carrier

    assert context_carrier.global_context is global_context
    assert context_carrier.request_context is request_context
    assert context_carrier.current() is request_context
    assert request_context.attrs == {"trace_id": "trace-1"}
    assert gateway_context.context_carrier is context_carrier


def test_package_exports_only_current_public_types() -> None:
    assert access.__all__ == [
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
