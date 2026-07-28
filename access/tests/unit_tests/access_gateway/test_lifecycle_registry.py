import logging

import pytest

from openjiuwen_runtime.access.lifecycle import (
    InfraContext,
    LifecycleHookRegistry,
    LifecyclePhase,
    MessageContext,
    UserRequest,
)


def _infra_context(phase: LifecyclePhase) -> InfraContext:
    return InfraContext(trace_id="trace-1", phase=phase)


def _message_context(phase: LifecyclePhase) -> MessageContext:
    return MessageContext(
        trace_id="trace-1",
        phase=phase,
        msg_id="message-1",
        msg_type="user_request",
        carrier="web",
    )


@pytest.mark.asyncio
async def test_registry_runs_global_and_phase_hooks_in_registration_order() -> None:
    registry = LifecycleHookRegistry()
    calls: list[str] = []

    async def global_hook(context: InfraContext) -> None:
        calls.append(f"global:{context.phase.value}")

    async def phase_hook(context: InfraContext) -> None:
        calls.append(f"phase:{context.phase.value}")

    registry.on_infra_all(global_hook, feature="metrics")
    registry.on_infra(
        LifecyclePhase.GATEWAY_START,
        phase_hook,
        feature="audit",
    )

    await registry.emit_infra(_infra_context(LifecyclePhase.GATEWAY_START))

    assert calls == ["global:gateway_start", "phase:gateway_start"]


@pytest.mark.asyncio
async def test_registry_runs_message_hooks_with_user_request() -> None:
    registry = LifecycleHookRegistry()
    request = UserRequest(raw={"text": "hello"})
    received: list[tuple[MessageContext, UserRequest]] = []

    async def capture(context: MessageContext, user_request: UserRequest) -> None:
        received.append((context, user_request))

    registry.on_message_all(capture, feature="metrics")
    registry.on_message(LifecyclePhase.ROUTE_BEFORE, capture, feature="audit")
    context = _message_context(LifecyclePhase.ROUTE_BEFORE)

    await registry.emit_message(context, request)

    assert received == [(context, request), (context, request)]


@pytest.mark.asyncio
async def test_off_removes_hooks_by_phase_and_feature() -> None:
    registry = LifecycleHookRegistry()
    calls: list[str] = []

    async def first(context: InfraContext) -> None:
        calls.append("first")

    async def second(context: InfraContext) -> None:
        calls.append("second")

    registry.on_infra(LifecyclePhase.GATEWAY_STOP, first, feature="metrics")
    registry.on_infra(LifecyclePhase.GATEWAY_STOP, second, feature="audit")
    registry.off(LifecyclePhase.GATEWAY_STOP, feature="metrics")

    await registry.emit_infra(_infra_context(LifecyclePhase.GATEWAY_STOP))
    assert calls == ["second"]

    registry.off(LifecyclePhase.GATEWAY_STOP)
    await registry.emit_infra(_infra_context(LifecyclePhase.GATEWAY_STOP))
    assert calls == ["second"]


@pytest.mark.asyncio
async def test_off_feature_removes_phase_and_global_hooks() -> None:
    registry = LifecycleHookRegistry()
    calls: list[str] = []

    async def infra_hook(context: InfraContext) -> None:
        calls.append("infra")

    async def message_hook(
        context: MessageContext,
        request: UserRequest,
    ) -> None:
        calls.append("message")

    registry.on_infra_all(infra_hook, feature="metrics")
    registry.on_infra(
        LifecyclePhase.GATEWAY_START,
        infra_hook,
        feature="metrics",
    )
    registry.on_message_all(message_hook, feature="metrics")
    registry.on_message(
        LifecyclePhase.ROUTE_AFTER,
        message_hook,
        feature="metrics",
    )
    registry.off_feature("metrics")

    await registry.emit_infra(_infra_context(LifecyclePhase.GATEWAY_START))
    await registry.emit_message(
        _message_context(LifecyclePhase.ROUTE_AFTER),
        UserRequest(),
    )

    assert calls == []


@pytest.mark.asyncio
async def test_hook_failure_is_isolated(caplog: pytest.LogCaptureFixture) -> None:
    registry = LifecycleHookRegistry()
    calls: list[str] = []

    async def failing(context: InfraContext) -> None:
        raise RuntimeError("hook failed")

    async def succeeding(context: InfraContext) -> None:
        calls.append("succeeded")

    registry.on_infra(LifecyclePhase.CHANNEL_CONNECT, failing)
    registry.on_infra(LifecyclePhase.CHANNEL_CONNECT, succeeding)

    with caplog.at_level(logging.ERROR):
        await registry.emit_infra(
            _infra_context(LifecyclePhase.CHANNEL_CONNECT),
        )

    assert calls == ["succeeded"]
    assert "Infra hook 执行失败" in caplog.text


def test_registry_rejects_hook_for_wrong_phase_group() -> None:
    registry = LifecycleHookRegistry()

    async def infra_hook(context: InfraContext) -> None:
        return None

    async def message_hook(
        context: MessageContext,
        request: UserRequest,
    ) -> None:
        return None

    with pytest.raises(ValueError, match="Infra"):
        registry.on_infra(LifecyclePhase.ROUTE_BEFORE, infra_hook)

    with pytest.raises(ValueError, match="Message"):
        registry.on_message(LifecyclePhase.GATEWAY_START, message_hook)
