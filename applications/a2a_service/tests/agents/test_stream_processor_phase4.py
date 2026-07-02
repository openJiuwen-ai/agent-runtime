# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""_StreamProcessor tests on the local EDPAgent event contract."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.EDPAgent.agent import _StreamProcessor
from agents.EDPAgent.events import (
    FinalAnswerChunkEvent,
    FinalAnswerEndEvent,
    FinalAnswerStartEvent,
    SummaryEvent,
    TodoListItemEvent,
)
from agents.EDPAgent.tool.lite_todo.models import configure_steps, reset_steps


@pytest.fixture(autouse=True)
def _configured_lite_todo_steps():
    configure_steps(
        [
            {"step_id": 1, "content": "Recommend product", "skill": "recommend"},
            {"step_id": 2, "content": "Confirm amount", "skill": "confirm"},
            {"step_id": 3, "content": "Submit order", "skill": "submit"},
            {"step_id": 4, "content": "Query balance", "skill": "balance"},
        ]
    )
    yield
    reset_steps()


@dataclass
class _FakeRawEvent:
    type: str
    payload: dict


def _feed_lite_todo(proc: _StreamProcessor, todos: list[dict]) -> list:
    return proc.process(
        _FakeRawEvent(
            type="tool_end",
            payload={
                "plugin": "lite_todo_write",
                "content": "",
                "data": {"todos": todos},
            },
        )
    )


def test_todolist_item_content_contains_html_br():
    proc = _StreamProcessor()
    events = _feed_lite_todo(
        proc,
        [
            {"step_id": 1, "status": "pending"},
            {"step_id": 2, "status": "pending"},
        ],
    )

    items = [event for event in events if isinstance(event, TodoListItemEvent)]

    assert len(items) == 2
    for item in items:
        assert item.content.endswith("<br/>")
        assert item.title in item.content
        assert "（待执行）" in item.content


def test_todolist_item_status_cn_mapping():
    proc = _StreamProcessor()
    events = _feed_lite_todo(
        proc,
        [
            {"step_id": 1, "status": "pending"},
            {"step_id": 2, "status": "in_progress"},
            {"step_id": 3, "status": "done"},
            {"step_id": 4, "status": "failed"},
        ],
    )

    contents = {item.id: item.content for item in events if isinstance(item, TodoListItemEvent)}

    assert "（待执行）" in contents[1]
    assert "（in_progress）" in contents[2]
    assert "（完成）" in contents[3]
    assert "（failed）" in contents[4]


def test_llm_output_emits_summary_events_not_chunk():
    proc = _StreamProcessor()
    pieces = ["already ", "completed ", "items"]

    collected = []
    for piece in pieces:
        collected.extend(proc.process(_FakeRawEvent(type="llm_output", payload={"content": piece})))

    assert isinstance(collected[0], FinalAnswerStartEvent)

    summaries = [event for event in collected if isinstance(event, SummaryEvent)]
    chunks = [event for event in collected if isinstance(event, FinalAnswerChunkEvent)]

    assert len(summaries) == 3
    assert [summary.content for summary in summaries] == pieces
    assert chunks == []


def test_answer_event_emits_final_chunk_plus_end():
    proc = _StreamProcessor()
    pieces = ["already ", "completed ", "items"]
    for piece in pieces:
        proc.process(_FakeRawEvent(type="llm_output", payload={"content": piece}))

    events = proc.process(_FakeRawEvent(type="answer", payload={"content": ""}))

    chunks = [event for event in events if isinstance(event, FinalAnswerChunkEvent)]
    ends = [event for event in events if isinstance(event, FinalAnswerEndEvent)]
    assert len(chunks) == 1
    assert len(ends) == 1
    assert chunks[0].content == "already completed items"


def test_answer_event_without_prior_output_still_emits_full_triple():
    proc = _StreamProcessor()
    events = proc.process(_FakeRawEvent(type="answer", payload={"content": "direct answer"}))

    starts = [event for event in events if isinstance(event, FinalAnswerStartEvent)]
    chunks = [event for event in events if isinstance(event, FinalAnswerChunkEvent)]
    ends = [event for event in events if isinstance(event, FinalAnswerEndEvent)]
    assert len(starts) == 1
    assert len(chunks) == 1
    assert len(ends) == 1
    assert chunks[0].content == "direct answer"


def test_flush_answer_if_interrupted_still_emits_chunk():
    proc = _StreamProcessor()
    proc.process(_FakeRawEvent(type="llm_output", payload={"content": "partial "}))
    proc.process(_FakeRawEvent(type="llm_output", payload={"content": "content"}))

    flushed = proc.finalize()

    chunks = [event for event in flushed if isinstance(event, FinalAnswerChunkEvent)]
    ends = [event for event in flushed if isinstance(event, FinalAnswerEndEvent)]
    assert len(chunks) == 1
    assert chunks[0].content == "partial content"
    assert len(ends) == 1
