# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2a.types.a2a_pb2 import (
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    StreamResponse,
    Task,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.struct_pb2 import Struct

import orchestrator.handlers.remote_agent_handler as remote_module
from orchestrator.route.normalized_event import NormalizedEvent, RouteTarget
from tests.framework_parallel._helpers import (
    FakeAsyncStream,
    FakeSubAgentClient,
    find_status_events,
    make_executor,
    make_turn_ctx,
    sr_artifact,
    sr_completed_text,
)


@pytest.fixture(autouse=True)
def _patch_remote_logging(monkeypatch):
    monkeypatch.setattr(remote_module, "to_logger", lambda *args, **kwargs: None)
    monkeypatch.setattr(remote_module, "build_versatile_start_observation", lambda **kwargs: "start")
    monkeypatch.setattr(remote_module, "build_versatile_end_observation", lambda **kwargs: "end")
    monkeypatch.setattr(
        remote_module,
        "get_settings",
        lambda: SimpleNamespace(versatile_adapter_url="http://va"),
    )


def _current_task(*, remote_task_id: str = "va-task", source_agent: str = "va") -> Task:
    meta = Struct()
    meta.update({"remote_task_id": remote_task_id, "source_agent": source_agent})
    return Task(id="task", metadata=meta, status=TaskStatus(state=TASK_STATE_INPUT_REQUIRED))


@pytest.mark.asyncio
async def test_handle_request_resume_missing_contexts_are_noops():
    executor = make_executor(sub_agent_client=FakeSubAgentClient(send=[FakeAsyncStream([])]))
    handler = executor._test_remote_handler

    await handler.handle(
        NormalizedEvent(type="request", data={}, metadata={}),
        RouteTarget(type="remote_agent"),
        {},
    )
    await handler.handle(
        NormalizedEvent(type="request", data={}, metadata={}),
        RouteTarget(type="remote_agent"),
        {"turn_ctx": make_turn_ctx(), "current_task": None},
    )


@pytest.mark.asyncio
async def test_continue_versatile_adapter_completed_resumes_executor():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_artifact({"event": "message", "data": {"text": "chunk"}}),
        sr_completed_text("workflow answer"),
    ])])
    executor = make_executor(
        sub_agent_client=client,
        redis_json={"params": {"p": "1"}, "trace_id": "t", "agent_id": "a"},
    )
    handler = executor._test_remote_handler
    handler._va_client = client
    turn_ctx = make_turn_ctx(conv_id="conv-1", task_id="task-1")
    run_agent = AsyncMock()

    await handler._continue_versatile_adapter(
        remote_module._ContinueVaRequest(
            turn_ctx=turn_ctx,
            va_task_id="va-task",
            user_input="next",
            headers={"h": "v"},
            original_body={"input": {"query": "next"}},
            context={"executor": SimpleNamespace(run_agent=run_agent)},
        )
    )

    run_agent.assert_awaited_once()
    assert run_agent.await_args.kwargs["cascade_result"] == {}


@pytest.mark.asyncio
async def test_continue_versatile_adapter_failed_and_still_input_required():
    failed_client = FakeSubAgentClient(send=[FakeAsyncStream([
        StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id="va",
                context_id="conv",
                status=TaskStatus(state=TASK_STATE_FAILED),
            )
        )
    ])])
    executor = make_executor(sub_agent_client=failed_client)
    handler = executor._test_remote_handler
    handler._va_client = failed_client
    turn_ctx = make_turn_ctx(conv_id="conv-1", task_id="task-1")

    await handler._continue_versatile_adapter(
        remote_module._ContinueVaRequest(
            turn_ctx=turn_ctx,
            va_task_id="va-task",
            user_input="next",
            headers={},
            original_body={},
            context={},
        )
    )
    statuses = find_status_events(turn_ctx.event_queue)
    assert any(event.status.state == TASK_STATE_FAILED for event in statuses)

    pending_client = FakeSubAgentClient(send=[FakeAsyncStream([])])
    executor = make_executor(sub_agent_client=pending_client)
    handler = executor._test_remote_handler
    handler._va_client = pending_client
    turn_ctx = make_turn_ctx(conv_id="conv-2", task_id="task-2")
    await handler._continue_versatile_adapter(
        remote_module._ContinueVaRequest(
            turn_ctx=turn_ctx,
            va_task_id="va-task",
            user_input="next",
            headers={},
            original_body={},
            context={},
        )
    )
    statuses = find_status_events(turn_ctx.event_queue)
    assert any(event.status.state == TASK_STATE_INPUT_REQUIRED for event in statuses)


@pytest.mark.asyncio
async def test_handle_request_resume_uses_current_task_metadata():
    client = FakeSubAgentClient(send=[FakeAsyncStream([sr_completed_text("done")])])
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    handler._va_client = client
    turn_ctx = make_turn_ctx(conv_id="conv-1", task_id="task-1")
    run_agent = AsyncMock()

    await handler.handle(
        NormalizedEvent(type="request", data={"user_input": "resume"}, metadata={}),
        RouteTarget(type="remote_agent", agent_key=""),
        {
            "turn_ctx": turn_ctx,
            "current_task": _current_task(),
            "executor": SimpleNamespace(run_agent=run_agent),
            "headers": {"h": "v"},
            "original_body": {"stream": False},
        },
    )

    run_agent.assert_awaited_once()
