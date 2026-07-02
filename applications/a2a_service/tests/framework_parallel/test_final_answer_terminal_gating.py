# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""final_answer_end stays a display frame on the dict-event contract."""
from __future__ import annotations

import pytest

from a2a.types.a2a_pb2 import TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from tests.framework_parallel._helpers import artifact_data, drain_queue, make_executor, make_turn_ctx


def _patch_agent_stream(monkeypatch, events: list[dict]) -> None:
    async def fake_agent_stream(**_kwargs):
        for event in events:
            yield event

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)


def _final_answer_artifacts(events: list) -> list[dict]:
    frames: list[dict] = []
    for event in events:
        if isinstance(event, TaskArtifactUpdateEvent):
            data = artifact_data(event)
            if data and data.get("type") == "final_answer_end":
                frames.append(data)
    return frames


def _status_events(events: list) -> list[TaskStatusUpdateEvent]:
    return [event for event in events if isinstance(event, TaskStatusUpdateEvent)]


@pytest.mark.asyncio
async def test_final_answer_end_is_display_artifact_not_control_status(monkeypatch):
    _patch_agent_stream(
        monkeypatch,
        [
            {"type": "final_answer_end", "data": {"content": "opening"}},
            {"type": "think_chunk", "data": {"content": "thinking"}},
            {"type": "final_answer_end", "data": {"content": "final report"}},
        ],
    )
    executor = make_executor()
    turn_ctx = make_turn_ctx()

    await executor.run_agent(turn_ctx, query="analyze", original_body={}, cascade_result=None)

    events = drain_queue(turn_ctx.event_queue)
    assert [frame["content"] for frame in _final_answer_artifacts(events)] == ["opening", "final report"]
    statuses = _status_events(events)
    assert len(statuses) == 1
    assert statuses[0].status.message.parts[0].text == "final report"


@pytest.mark.asyncio
async def test_final_answer_chunk_survives_when_end_content_is_empty(monkeypatch):
    _patch_agent_stream(
        monkeypatch,
        [
            {"type": "final_answer_chunk", "data": {"content": "final report"}},
            {"type": "final_answer_end", "data": {"content": ""}},
        ],
    )
    executor = make_executor()
    turn_ctx = make_turn_ctx()

    await executor.run_agent(turn_ctx, query="analyze", original_body={}, cascade_result=None)

    artifacts = [
        artifact_data(event)
        for event in drain_queue(turn_ctx.event_queue)
        if isinstance(event, TaskArtifactUpdateEvent)
    ]
    assert {"type": "final_answer_chunk", "content": "final report"} in artifacts
    assert {"type": "final_answer_end", "content": ""} in artifacts
