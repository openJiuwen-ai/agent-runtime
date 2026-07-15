# coding: utf-8
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from a2a.server.events import EventQueue
from google.protobuf.json_format import MessageToDict

from orchestrator.handlers.requester_handler import RequesterHandler
from orchestrator.route import NormalizedEvent, RouteTarget


def _collect_emitted_events(event_queue: EventQueue) -> list:
    events = []
    inner = getattr(event_queue, "_queue", None) or getattr(event_queue, "queue", None)
    if inner is None:
        return events
    try:
        while True:
            events.append(inner.get_nowait())
    except asyncio.QueueEmpty:
        return events


def _event_data_of(a2a_event) -> dict:
    artifact = getattr(a2a_event, "artifact", None)
    if artifact is None:
        return {}
    for part in artifact.parts:
        if part.WhichOneof("content") == "data":
            data = MessageToDict(part.data)
            return data if isinstance(data, dict) else {}
    return {}


async def _dispatch_tool_start(
    handler: RequesterHandler,
    event_queue: EventQueue,
    step_counter: list[int],
    *,
    content: str,
    plugin: str,
) -> None:
    await handler.handle(
        NormalizedEvent(
            type="tool_start",
            data={
                "raw_event": {
                    "type": "tool_start",
                    "data": {"content": content, "plugin": plugin},
                }
            },
            metadata={"source": "local_agent", "task_id": "task-1"},
        ),
        RouteTarget(type="requester"),
        {
            "event_queue": event_queue,
            "task_id": "task-1",
            "conv_id": "conv-1",
            "step_counter": step_counter,
        },
    )


@pytest.mark.asyncio
async def test_planning_event_emitted_before_each_tool_start():
    """RequesterHandler emits planning_execution_process immediately before each tool_start."""
    handler = RequesterHandler(MagicMock())
    event_queue = EventQueue()
    step_counter = [0]

    await _dispatch_tool_start(
        handler, event_queue, step_counter, content="query balance", plugin="query_balance"
    )
    await _dispatch_tool_start(
        handler, event_queue, step_counter, content="transfer", plugin="transfer"
    )

    emitted = _collect_emitted_events(event_queue)
    types = [_event_data_of(e).get("type") for e in emitted]

    assert types == [
        "planning_execution_process",
        "tool_start",
        "planning_execution_process",
        "tool_start",
    ]


@pytest.mark.asyncio
async def test_step_counter_increments_with_each_tool_start():
    """Planning content carries the incrementing step number and tool name."""
    handler = RequesterHandler(MagicMock())
    event_queue = EventQueue()
    step_counter = [0]

    await _dispatch_tool_start(handler, event_queue, step_counter, content="A", plugin="tool_a")
    await _dispatch_tool_start(handler, event_queue, step_counter, content="B", plugin="tool_b")

    planning_contents = [
        _event_data_of(ev).get("content", "")
        for ev in _collect_emitted_events(event_queue)
        if _event_data_of(ev).get("type") == "planning_execution_process"
    ]

    assert len(planning_contents) == 2
    assert "1" in planning_contents[0]
    assert "(tool=tool_a)" in planning_contents[0]
    assert "2" in planning_contents[1]
    assert "(tool=tool_b)" in planning_contents[1]


class _HeartbeatStub:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.attach_called = 0
        self._forward_first_end = True

    async def start_heartbeat(self, request_id: str, source: str = "a2a_service"):
        self.started += 1
        return {"success": True, "code": "HEARTBEAT_STARTED"}

    async def stop_heartbeat(self, request_id: str, *, reason: str = "", mark_end: bool = True):
        self.stopped += 1
        forward = self._forward_first_end
        self._forward_first_end = False
        return {
            "success": True,
            "code": "HEARTBEAT_STOPPED" if forward else "HEARTBEAT_STOP_NOOP",
            "forward_to_frontend": forward,
        }

    async def attach_seq(self, raw_event: dict, request_id: str):
        self.attach_called += 1
        raw_event.setdefault("data", {})["seq"] = raw_event.get("data", {}).get("seq", 1)


@pytest.mark.asyncio
async def test_heartbeat_initial_triggers_start_and_forward():
    handler = RequesterHandler(MagicMock())
    event_queue = EventQueue()
    hb = _HeartbeatStub()

    await handler.handle(
        NormalizedEvent(
            type="heartbeat",
            data={
                "raw_event": {
                    "type": "heartbeat",
                    "data": {
                        "data": {
                            "request_id": "conv-1",
                            "heartbeat_type": "initial",
                            "status": "processing",
                        }
                    },
                }
            },
            metadata={"source": "local_agent", "task_id": "task-1"},
        ),
        RouteTarget(type="requester"),
        {
            "event_queue": event_queue,
            "task_id": "task-1",
            "conv_id": "conv-1",
            "heartbeat_runtime": hb,
        },
    )

    emitted = _collect_emitted_events(event_queue)
    assert hb.started == 1
    assert hb.attach_called == 1
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_heartbeat_duplicate_end_is_suppressed():
    handler = RequesterHandler(MagicMock())
    event_queue = EventQueue()
    hb = _HeartbeatStub()

    event = NormalizedEvent(
        type="heartbeat",
        data={
            "raw_event": {
                "type": "heartbeat",
                "data": {
                    "request_id": "conv-2",
                    "heartbeat_type": "end",
                    "status": "completed",
                },
            }
        },
        metadata={"source": "local_agent", "task_id": "task-2"},
    )

    await handler.handle(
        event,
        RouteTarget(type="requester"),
        {
            "event_queue": event_queue,
            "task_id": "task-2",
            "conv_id": "conv-2",
            "heartbeat_runtime": hb,
        },
    )
    await handler.handle(
        event,
        RouteTarget(type="requester"),
        {
            "event_queue": event_queue,
            "task_id": "task-2",
            "conv_id": "conv-2",
            "heartbeat_runtime": hb,
        },
    )

    emitted = _collect_emitted_events(event_queue)
    assert hb.stopped == 2
    assert len(emitted) == 1
