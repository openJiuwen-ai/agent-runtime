# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import sys
import types

import pytest

from orchestrator.handlers.local_agent_handler import LocalAgentHandler
from orchestrator.route.handler_registry import HandlerRegistry
from orchestrator.route.normalized_event import NormalizedEvent, RouteContext, RouteTarget
from orchestrator.route.route_dispatcher import RouteDispatcher
from orchestrator.route.route_profiles import RouteConfig, RouteConfigLoader, SourceRouteProfile
from orchestrator.state.task_state_manager import TaskStateManager


class _Executor:
    def __init__(self) -> None:
        self.calls = []

    async def run_agent(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.mark.asyncio
async def test_local_agent_handler_request_and_completed_paths():
    state = TaskStateManager()
    handler = LocalAgentHandler(state, local_agent_name="DPA")
    executor = _Executor()

    await handler.handle(
        NormalizedEvent(type="request", data={}, metadata={}),
        RouteTarget(type="local_agent"),
        {
            "task_id": "task-1",
            "conv_id": "conv-1",
            "executor": executor,
            "turn_ctx": object(),
            "query": "hello",
            "original_body": {"x": 1},
            "step_counter": [3],
        },
    )

    saved = await state.get_task("task-1")
    assert saved["metadata"]["source_agent"] == "DPA"
    assert executor.calls[0][1]["query"] == "hello"

    await handler.handle(
        NormalizedEvent(type="completed", data={}, metadata={}),
        RouteTarget(type="local_agent"),
        {"task_id": "task-1", "conv_id": "conv-1"},
    )
    assert (await state.get_task("task-1"))["status_state"] == "COMPLETED"


def test_handler_registry_loads_filters_and_handles_errors(monkeypatch):
    module = types.ModuleType("tests.dynamic_handlers")

    class GoodHandler:
        def __init__(self, state_manager=None):
            self.state_manager = state_manager

        async def handle(self, *_args):
            return "ok"

    class KwHandler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def handle(self, *_args):
            return "kw"

    class BadSignature:
        __init__ = 3

        async def handle(self, *_args):
            return "bad"

    module.GoodHandler = GoodHandler
    module.KwHandler = KwHandler
    module.BadSignature = BadSignature
    monkeypatch.setitem(sys.modules, "tests.dynamic_handlers", module)

    registry = HandlerRegistry()
    handlers = registry.load_handlers(
        {
            "good": "tests.dynamic_handlers.GoodHandler",
            "kw": "tests.dynamic_handlers.KwHandler",
            "bad": "tests.dynamic_handlers.Missing",
        },
        state_manager="state",
        ignored="ignored",
    )

    assert sorted(handlers) == ["good", "kw"]
    assert registry._filter_kwargs(GoodHandler, {"state_manager": 1, "ignored": 2}) == {"state_manager": 1}
    assert registry._filter_kwargs(KwHandler, {"a": 1}) == {"a": 1}
    assert registry._filter_kwargs(BadSignature, {"a": 1}) == {"a": 1}
    registry.register_handler_class("x", GoodHandler)
    assert registry._handler_classes["x"] is GoodHandler


@pytest.mark.asyncio
async def test_route_dispatcher_config_registration_and_errors(monkeypatch, tmp_path):
    module = types.ModuleType("tests.dispatch_handlers")

    class RequesterHandler:
        async def handle(self, event, target, context):
            return {"event": event.type, "target": target.type, "task": context["task_id"]}

    module.RequesterHandler = RequesterHandler
    monkeypatch.setitem(sys.modules, "tests.dispatch_handlers", module)

    config = RouteConfig(handlers={"local_agent": "tests.dispatch_handlers.RequesterHandler"})
    dispatcher = RouteDispatcher(TaskStateManager(), config=config, local_agent_names=["DPA"])
    dispatcher.register_handlers_from_config()
    result = await dispatcher.dispatch(
        NormalizedEvent(type="request", data={}, metadata={"source": "requester"}),
        {"task_id": "task-1", "root_task_id": "task-1", "conv_id": "conv-1"},
    )
    assert result == {"event": "request", "target": "local_agent", "task": "task-1"}

    with pytest.raises(ValueError, match="Unknown source direction"):
        await dispatcher.route(
            NormalizedEvent(type="x", data={}, metadata={"source": "unknown"}),
            RouteContext(root_task_id="r"),
        )

    empty = RouteDispatcher(TaskStateManager(), config=RouteConfig())
    with pytest.raises(ValueError, match="No handler registered"):
        await empty.dispatch(NormalizedEvent(type="request", data={}, metadata={}), {"root_task_id": "r"})

    config_path = tmp_path / "route.yaml"
    config_path.write_text(
        "handlers:\n"
        "  requester: tests.dispatch_handlers.RequesterHandler\n"
        "profiles:\n"
        "  agent-x: {}\n"
        "max_cascade_depth: 2\n",
        encoding="utf-8",
    )
    loaded = dispatcher.load_config(str(config_path))
    assert loaded["requester"] == "tests.dispatch_handlers.RequesterHandler"
    assert isinstance(dispatcher.get_profile("agent-x"), SourceRouteProfile)
    assert RouteConfigLoader.resolve_profile(dispatcher.config, "missing") is dispatcher.config.default_profile
