# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Executor protocol terminal status tests."""
from __future__ import annotations

import pytest

from a2a.types.a2a_pb2 import (
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    TASK_STATE_COMPLETED,
)

from tests.framework_parallel._helpers import artifact_data, drain_queue, make_executor, make_turn_ctx


def _patch_agent_stream(monkeypatch, events: list[dict]) -> None:
    async def fake_agent_stream(**_kwargs):
        for event in events:
            yield event

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)


def _status_events(events: list) -> list[TaskStatusUpdateEvent]:
    return [event for event in events if isinstance(event, TaskStatusUpdateEvent)]


def _artifact_frames(events: list) -> list[dict]:
    frames = []
    for event in events:
        if not isinstance(event, TaskArtifactUpdateEvent):
            continue
        frame = artifact_data(event)
        if frame is not None:
            frames.append(frame)
    return frames


@pytest.mark.asyncio
async def test_run_agent_enqueues_completed_status_on_normal_end(monkeypatch):
    _patch_agent_stream(
        monkeypatch,
        [{"type": "final_answer_end", "data": {"content": "done"}}],
    )
    executor = make_executor()
    turn_ctx = make_turn_ctx()

    await executor.run_agent(turn_ctx, query="q", original_body={}, cascade_result=None)

    statuses = _status_events(drain_queue(turn_ctx.event_queue))
    assert len(statuses) == 1
    assert statuses[0].status.state == TASK_STATE_COMPLETED
    assert statuses[0].status.message.parts[0].text == "done"


@pytest.mark.asyncio
async def test_completed_status_prefers_final_answer_chunk(monkeypatch):
    _patch_agent_stream(
        monkeypatch,
        [
            {"type": "final_answer_chunk", "data": {"content": "final chunk"}},
            {"type": "final_answer_end", "data": {"content": ""}},
        ],
    )
    executor = make_executor()
    turn_ctx = make_turn_ctx()

    await executor.run_agent(turn_ctx, query="q", original_body={}, cascade_result=None)

    events = drain_queue(turn_ctx.event_queue)
    statuses = _status_events(events)
    assert statuses[0].status.message.parts[0].text == "final chunk"
    assert {"type": "final_answer_end", "content": ""} in _artifact_frames(events)


@pytest.mark.asyncio
async def test_final_answer_end_remains_display_artifact(monkeypatch):
    _patch_agent_stream(
        monkeypatch,
        [{"type": "final_answer_end", "data": {"content": "display and terminal"}}],
    )
    executor = make_executor()
    turn_ctx = make_turn_ctx()

    await executor.run_agent(turn_ctx, query="q", original_body={}, cascade_result=None)

    events = drain_queue(turn_ctx.event_queue)
    assert {"type": "final_answer_end", "content": "display and terminal"} in _artifact_frames(events)
    assert _status_events(events)[0].status.message.parts[0].text == "display and terminal"
