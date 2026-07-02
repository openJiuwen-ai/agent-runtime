# coding: utf-8
from __future__ import annotations

import pytest
from a2a.types.a2a_pb2 import TaskArtifactUpdateEvent
from google.protobuf.json_format import MessageToDict

from channels.dict_to_a2a import dict_to_a2a


def _artifact_data(event: TaskArtifactUpdateEvent) -> dict:
    for part in event.artifact.parts:
        if part.WhichOneof("content") == "data":
            data = MessageToDict(part.data)
            return data if isinstance(data, dict) else {}
    return {}


def test_dict_event_maps_to_a2a_artifact_with_metadata_type():
    event = dict_to_a2a(
        {"type": "think_chunk", "data": {"content": "hi"}},
        "task-1",
        "conv-1",
    )

    assert isinstance(event, TaskArtifactUpdateEvent)
    assert event.task_id == "task-1"
    assert event.context_id == "conv-1"
    assert _artifact_data(event) == {"type": "think_chunk", "content": "hi"}
    assert MessageToDict(event.artifact.metadata) == {"type": "think_chunk"}


def test_sub_task_dict_preserves_path_kind_and_inner_frame():
    event = dict_to_a2a(
        {
            "type": "sub_task",
            "data": {
                "sub_task_path": ["A", "wf:a"],
                "node_kind": "workflow",
                "data": {"event": "message", "data": {"node_type": "LLM", "text": "x"}},
            },
        },
        "task-1",
        "conv-1",
    )

    assert _artifact_data(event) == {
        "type": "sub_task",
        "sub_task_path": ["A", "wf:a"],
        "node_kind": "workflow",
        "data": {"event": "message", "data": {"node_type": "LLM", "text": "x"}},
    }


def test_sub_task_dict_preserves_agent_inner_frame():
    event = dict_to_a2a(
        {
            "type": "sub_task",
            "data": {
                "sub_task_path": ["A"],
                "node_kind": "agent",
                "data": {"type": "think_chunk", "content": "child thinking"},
            },
        },
        "task-1",
        "conv-1",
    )

    assert _artifact_data(event) == {
        "type": "sub_task",
        "sub_task_path": ["A"],
        "node_kind": "agent",
        "data": {"type": "think_chunk", "content": "child thinking"},
    }


def test_plain_agent_dict_is_not_sub_task():
    event = dict_to_a2a(
        {"type": "think_chunk", "data": {"content": "hi"}},
        "task-1",
        "conv-1",
    )

    data = _artifact_data(event)
    assert data == {"type": "think_chunk", "content": "hi"}
    assert data.get("type") != "sub_task"
    assert event.artifact.metadata["type"] == "think_chunk"


def test_dict_to_a2a_defaults_none_data_to_empty_dict():
    event = dict_to_a2a({"type": "conversation_start", "data": None}, "task-1", "conv-1")

    assert _artifact_data(event) == {"type": "conversation_start"}


def test_dict_to_a2a_rejects_missing_type():
    with pytest.raises(ValueError):
        dict_to_a2a({"data": {}}, "task-1", "conv-1")


def test_dict_to_a2a_rejects_non_dict_data():
    with pytest.raises(TypeError):
        dict_to_a2a({"type": "x", "data": "bad"}, "task-1", "conv-1")
