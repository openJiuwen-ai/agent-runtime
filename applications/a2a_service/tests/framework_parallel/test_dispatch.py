# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""RemoteAgentHandler parallel sub-agent dispatch tests."""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import asyncio

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
)


def _specs(*ids):
    return [
        {
            "entity_id": entity_id,
            "entity_name": f"Entity {entity_id}",
            "query": f"Analyze {entity_id}",
            "url": "",
        }
        for entity_id in ids
    ]


async def test_concurrency_truncation_keeps_first_n():
    executor = make_executor(sub_agent_client=None, max_concurrent_sub_agents=3)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx()

    await handler._handle_sub_agent_dispatch(
        {"raw_event": {"type": "sub_agent_dispatch", "data": {"specs": _specs("A", "B", "C", "D", "E")}}},
        {"turn_ctx": ctx, "executor": None},
    )

    # No executor means cascade is not resumed, but dispatched/skipped state is still observable
    # through emitted lifecycle frames for the first three specs.
    starts = [frame for frame in collect_sub_tasks(ctx.event_queue) if frame["data"].get("event") == "node_start"]
    assert [frame["sub_task_path"][0] for frame in starts] == ["A", "B", "C"]


async def test_dispatch_respects_max_call_depth_without_emitting_nodes():
    executor = make_executor(sub_agent_client=None, max_call_depth=1)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(sub_task_path=("A",))
    capture = {}

    class _Executor:
        async def run_agent(self, _turn_ctx, *, query, original_body, cascade_result, step_counter=None):
            capture["cascade"] = cascade_result

    await handler._handle_sub_agent_dispatch(
        {"raw_event": {"type": "sub_agent_dispatch", "data": {"specs": _specs("B", "C")}}},
        {"turn_ctx": ctx, "executor": _Executor()},
    )

    assert collect_sub_tasks(ctx.event_queue) == []
    assert capture["cascade"]["sub_agent_results"] == []
    assert [s["reason"] for s in capture["cascade"]["skipped_entities"]] == [
        "max_call_depth",
        "max_call_depth",
    ]


async def test_no_sub_agent_client_degrades_to_failed():
    executor = make_executor(sub_agent_client=None)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx()

    result = await handler._run_one_sub_agent(_specs("A")[0], ctx)

    assert result["status"] == "failed"
    assert "client factory unavailable" in result["error"]


async def test_run_sub_agent_done_emits_lifecycle_and_captures_child_id():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-1"),
        sr_artifact({"type": "think_chunk", "content": "analyzing"}),
        sr_completed_text("entity A answer"),
    ])])
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(conv_id="conv-1")

    result = await handler._run_one_sub_agent(_specs("A")[0], ctx)

    assert result["status"] == "done"
    assert result["content"] == "entity A answer"
    assert result["child_task_id"] == "child-1"

    frames = collect_sub_tasks(ctx.event_queue)
    assert frames[0]["data"] == {"event": "node_start", "entity_name": "Entity A"}
    assert frames[1]["data"] == {"type": "think_chunk", "content": "analyzing"}
    assert frames[-1]["data"] == {"event": "node_end", "status": "done", "content": "entity A answer"}


async def test_run_sub_agent_cancelled_emits_legacy_reason():
    executor = make_executor(sub_agent_client=FakeSubAgentClient(send=[]))
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(conv_id="conv-1")
    cancel_event = asyncio.Event()

    async def cancelled_drive(*_args, **_kwargs):
        cancel_event.set()
        return {"content": "", "child_task_id": "child-1"}

    handler._drive_sub_agent = cancelled_drive

    result = await handler._run_one_sub_agent(_specs("A")[0], ctx, cancel_event)

    assert result["status"] == "cancelled"
    assert result["child_task_id"] == "child-1"
    assert collect_sub_tasks(ctx.event_queue)[-1]["data"] == {
        "event": "node_end",
        "status": "cancelled",
        "reason": "用户取消",
    }


async def test_sub_agent_dispatch_resumes_with_cancelled_cascade():
    executor = make_executor(sub_agent_client=None)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(conv_id="conv-1")
    capture = {}

    async def cancelled_run_one(spec, turn_ctx, cancel_event=None):
        await handler._emit_sub_task(
            turn_ctx,
            [spec["entity_id"]],
            "agent",
            {"event": "node_end", "status": "cancelled", "reason": "用户取消"},
        )
        return {
            "entity_id": spec["entity_id"],
            "status": "cancelled",
            "content": "",
            "error": "sub agent cancelled",
            "child_task_id": "child-" + spec["entity_id"],
        }

    class _Executor:
        async def run_agent(self, _turn_ctx, *, query, original_body, cascade_result, step_counter=None):
            capture["cascade"] = cascade_result
            capture["step_counter"] = step_counter

    handler._run_one_sub_agent = cancelled_run_one

    await handler._handle_sub_agent_dispatch(
        {"raw_event": {"type": "sub_agent_dispatch", "data": {"specs": _specs("A", "B")}}},
        {"turn_ctx": ctx, "executor": _Executor(), "step_counter": [3]},
    )

    assert [r["status"] for r in capture["cascade"]["sub_agent_results"]] == ["cancelled", "cancelled"]
    assert capture["step_counter"] == [3]
    assert collect_sub_tasks(ctx.event_queue)[-1]["data"] == {
        "event": "node_end",
        "status": "cancelled",
        "reason": "用户取消",
    }


async def test_run_sub_agent_uses_parent_path_prefix():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-1"),
        sr_completed_text("entity B answer"),
    ])])
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(conv_id="conv-1", sub_task_path=("A",))

    await handler._run_one_sub_agent(_specs("B")[0], ctx)

    frames = collect_sub_tasks(ctx.event_queue)
    assert {tuple(frame["sub_task_path"]) for frame in frames} == {("A", "B")}


async def test_run_sub_agent_failure_marks_failed():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-9"),
    ], exc=RuntimeError("api failed"))])
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx()

    result = await handler._run_one_sub_agent(_specs("A")[0], ctx)

    assert result["status"] == "failed"
    assert "api failed" in result["error"]
    assert collect_sub_tasks(ctx.event_queue)[-1]["data"] == {
        "event": "node_end",
        "status": "failed",
        "error": "api failed",
    }


async def test_run_sub_agent_failed_status_not_silent_done():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-1"),
        sr_failed("子Agent内部异常"),
    ])])
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx()

    result = await handler._run_one_sub_agent(_specs("A")[0], ctx)

    assert result["status"] == "failed"
    assert "子Agent内部异常" in result["error"]
    assert result["child_task_id"] == ""
    frames = collect_sub_tasks(ctx.event_queue)
    assert frames[-1]["data"]["event"] == "node_end"
    assert frames[-1]["data"]["status"] == "failed"


async def test_run_sub_agent_passes_through_already_stamped_deep_frame():
    client = FakeSubAgentClient(send=[FakeAsyncStream([
        sr_task("child-1"),
        sr_artifact({
            "type": "sub_task",
            "sub_task_path": ["A", "X"],
            "node_kind": "agent",
            "data": {"type": "think_chunk", "content": "grandchild"},
        }),
        sr_completed_text("done"),
    ])])
    executor = make_executor(sub_agent_client=client)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx()

    await handler._run_one_sub_agent(_specs("A")[0], ctx)

    deep = [frame for frame in collect_sub_tasks(ctx.event_queue) if frame["sub_task_path"] == ["A", "X"]]
    assert len(deep) == 1
    assert deep[0]["data"] == {"type": "think_chunk", "content": "grandchild"}


async def test_sub_agent_timeout():
    client = FakeSubAgentClient(send=[FakeAsyncStream([])])
    executor = make_executor(sub_agent_client=client, sub_agent_timeout_seconds=0.01)
    handler = executor._test_remote_handler

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(1)
        return {"content": "late", "child_task_id": "child"}

    handler._drive_sub_agent = slow
    ctx = make_turn_ctx()

    result = await handler._run_one_sub_agent(_specs("A")[0], ctx)

    assert result["status"] == "timeout"
    assert collect_sub_tasks(ctx.event_queue)[-1]["data"] == {
        "event": "node_end",
        "status": "timeout",
        "error": "sub agent timeout",
    }
