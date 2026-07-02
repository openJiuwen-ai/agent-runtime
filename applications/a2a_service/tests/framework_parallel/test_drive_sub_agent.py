# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""RemoteAgentHandler sub-agent stream driving tests."""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import pytest
import httpx
from a2a.types.a2a_pb2 import StreamResponse, Task
from google.protobuf.json_format import MessageToDict
from a2a.utils.errors import UnsupportedOperationError

import orchestrator.handlers.remote_agent_handler as remote_module
from tests.framework_parallel._helpers import (
    FakeAsyncStream,
    FakeSubAgentClient,
    collect_sub_tasks,
    make_executor,
    make_turn_ctx,
    sr_artifact,
    sr_completed_text,
    sr_failed,
    sr_task,
    task_completed_text,
)

_SPEC = {"entity_id": "A", "entity_name": "Entity A", "query": "Analyze A", "url": ""}


async def _drive(executor):
    ctx = make_turn_ctx()
    handler = executor._test_remote_handler
    client = handler._sub_agent_clients[""]
    result = await handler._drive_sub_agent(
        remote_module._DriveSubAgentRequest(
            client=client,
            spec=_SPEC,
            query="Analyze A",
            turn_ctx=ctx,
            path=["A"],
        )
    )
    return result, collect_sub_tasks(ctx.event_queue)


async def test_stream_dispatch_by_whichoneof_happy_path():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-1"),
        sr_artifact({"type": "think_chunk", "content": "analyzing A"}),
        sr_completed_text("entity A final answer"),
    ])])
    executor = make_executor(sub_agent_client=client)

    result, frames = await _drive(executor)

    assert result == {"content": "entity A final answer", "child_task_id": "child-1"}
    assert frames == [
        {
            "type": "sub_task",
            "sub_task_path": ["A"],
            "node_kind": "agent",
            "data": {"type": "think_chunk", "content": "analyzing A"},
        }
    ]


async def test_sub_agent_request_inherits_body_and_uses_safe_context_id():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-1"),
        sr_completed_text("done"),
    ])])
    executor = make_executor(
        sub_agent_client=client,
        redis_json={
            "headers": {"Authorization": "Bearer token"},
            "params": {"workspace_id": "ws"},
            "trace_id": "trace-1",
            "body": {"input": {"account": "001"}},
        },
    )
    ctx = make_turn_ctx(conv_id="conv_1")
    handler = executor._test_remote_handler

    result = await handler._drive_sub_agent(
        remote_module._DriveSubAgentRequest(
            client=client,
            spec=_SPEC,
            query="Analyze A",
            turn_ctx=ctx,
            path=["A"],
        )
    )

    assert result == {"content": "done", "child_task_id": "child-1"}
    request = client.send_requests[0]
    assert request.message.context_id == "conv_1-sub-A"
    assert ":" not in request.message.context_id
    data_part = next(part for part in request.message.parts if part.WhichOneof("content") == "data")
    session_context = MessageToDict(data_part.data)["session_context"]
    assert session_context["body"] == {"input": {"account": "001"}}
    assert session_context["sub_task_path"] == ["A"]


def test_streamresponse_is_not_task_instance():
    sr = StreamResponse(task=Task(id="x"))
    assert sr.WhichOneof("payload") == "task"
    assert not isinstance(sr, Task)


# ── 问题 1：子 Agent FAILED 终态 → __terminal__，不可落流末当 done ────────────


async def test_failed_status_raises_not_silent_done():
    """子 Agent 返回 FAILED status_update → 抛错，不会落流末当 done。"""
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-1"),
        sr_failed("子Agent内部异常"),
    ])])
    executor = make_executor(sub_agent_client=client)

    with pytest.raises(RuntimeError, match="子Agent内部异常"):
        await _drive(executor)


# ── RECONN-03：断连期间已完成 → resubscribe 抛错 → tasks/get 回退 ────────────


async def test_resubscribe_unsupported_falls_back_to_tasks_get():
    client = FakeSubAgentClient(
        send=[FakeAsyncStream([sr_task("child-1")], exc=UnsupportedOperationError())],
        get_task=task_completed_text("child-1", "completed during disconnect"),
    )
    executor = make_executor(sub_agent_client=client)

    result, _frames = await _drive(executor)

    assert result == {"content": "completed during disconnect", "child_task_id": "child-1"}
    assert client.get_task_calls == 1


async def test_network_disconnect_resubscribes_and_returns_completed(monkeypatch):
    sleeps: list[int] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("orchestrator.handlers.remote_agent_handler.asyncio.sleep", fake_sleep)
    client = FakeSubAgentClient(
        send=[
            FakeAsyncStream(
                [sr_task("child-1")],
                exc=httpx.ReadError("stream disconnected"),
            )
        ],
        subscribe=[FakeAsyncStream([sr_completed_text("completed after subscribe")])],
    )
    executor = make_executor(sub_agent_client=client)

    result, _frames = await _drive(executor)

    assert result == {"content": "completed after subscribe", "child_task_id": "child-1"}
    assert client.subscribe_calls == 1
    assert sleeps == [2]


async def test_network_disconnect_retry_exhausted_falls_back_to_tasks_get(monkeypatch):
    sleeps: list[int] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("orchestrator.handlers.remote_agent_handler.asyncio.sleep", fake_sleep)
    client = FakeSubAgentClient(
        send=[
            FakeAsyncStream(
                [sr_task("child-1")],
                exc=httpx.RemoteProtocolError("stream disconnected"),
            )
        ],
        subscribe=[
            FakeAsyncStream([], exc=httpx.ReadError("again")),
            FakeAsyncStream([], exc=httpx.ReadError("again")),
            FakeAsyncStream([], exc=httpx.ReadError("again")),
            FakeAsyncStream([], exc=httpx.ReadError("again")),
        ],
        get_task=task_completed_text("child-1", "completed via get"),
    )
    executor = make_executor(sub_agent_client=client)

    result, _frames = await _drive(executor)

    assert result == {"content": "completed via get", "child_task_id": "child-1"}
    assert client.subscribe_calls == 3
    assert client.get_task_calls == 1
    assert sleeps == [2, 4, 8]


async def test_network_disconnect_retry_exhausted_and_tasks_get_empty_reraises(monkeypatch):
    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr("orchestrator.handlers.remote_agent_handler.asyncio.sleep", fake_sleep)
    client = FakeSubAgentClient(
        send=[
            FakeAsyncStream(
                [sr_task("child-1")],
                exc=httpx.ReadError("stream disconnected"),
            )
        ],
        subscribe=[
            FakeAsyncStream([], exc=httpx.ReadError("again")),
            FakeAsyncStream([], exc=httpx.ReadError("again")),
            FakeAsyncStream([], exc=httpx.ReadError("again")),
            FakeAsyncStream([], exc=httpx.ReadError("again")),
        ],
        get_task=None,
    )
    executor = make_executor(sub_agent_client=client)

    with pytest.raises(httpx.ReadError):
        await _drive(executor)

    assert client.get_task_calls == 1


async def test_resubscribe_unsupported_and_tasks_get_empty_reraises():
    client = FakeSubAgentClient(
        send=[FakeAsyncStream([sr_task("child-1")], exc=UnsupportedOperationError())],
        get_task=None,
    )
    executor = make_executor(sub_agent_client=client)
    with pytest.raises(UnsupportedOperationError):
        await _drive(executor)
