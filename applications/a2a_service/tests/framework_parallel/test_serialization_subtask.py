# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Northbound serialization on the Channel/Normalizer/dict-event boundary."""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import json

from a2a.types.a2a_pb2 import TaskStatus, TaskStatusUpdateEvent, TASK_STATE_COMPLETED
from channels.dict_to_a2a import dict_to_a2a
from channels.mobile_bank_channel import MobileBankChannel
from channels.normalizer import EventNormalizer
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct

from tests.framework_parallel._helpers import (
    artifact_event,
    status_completed_data,
    status_completed_empty,
    status_completed_text,
)


CHANNEL = MobileBankChannel()


def _format(event: dict) -> dict:
    wrapped = CHANNEL.format_event(
        event,
        agent_id="ag",
        conversation_id="conv-1",
        elapsed=0.1,
    )
    assert wrapped is not None
    return wrapped


def _sub_task(sub_task_path: list[str], node_kind: str, data: dict) -> dict:
    return {
        "type": "sub_task",
        "data": {
            "sub_task_path": sub_task_path,
            "node_kind": node_kind,
            "data": data,
        },
    }


def test_inner_kind_agent_frame():
    meta = CHANNEL._extract_inner_meta({"type": "think_chunk", "content": "hi"}, node_kind="agent")
    assert meta["kind"] == "agent"
    assert meta["type"] == "think_chunk"
    assert meta["content"] == "hi"


def test_inner_kind_lifecycle_node_start_and_end():
    start = CHANNEL._extract_inner_meta({"event": "node_start", "entity_name": "A"}, node_kind="agent")
    end = CHANNEL._extract_inner_meta({"event": "node_end", "status": "done"}, node_kind="workflow")
    assert start["kind"] == "lifecycle"
    assert end["kind"] == "lifecycle"


def test_inner_kind_workflow_message():
    meta = CHANNEL._extract_inner_meta(
        {"event": "message", "data": {"node_type": "LLM", "text": "x"}},
        node_kind="workflow",
    )
    assert meta["kind"] == "workflow"
    assert meta["type"] == "message"
    assert meta["data"] == {"node_type": "LLM", "text": "x"}


def test_dict_to_a2a_and_normalizer_preserve_sub_task_path_and_kind():
    event = _sub_task(["A"], "agent", {"event": "node_start", "entity_name": "A"})

    normalized = EventNormalizer.normalize(dict_to_a2a(event, "t", "c"))

    assert normalized == event


def test_roundtrip_agent_report_frame():
    wrapped = _format(_sub_task(["A"], "agent", {"type": "think_chunk", "content": "analyzing A"}))

    crd = wrapped["custom_rsp_data"]
    assert crd["event"] == "sub_task"
    assert crd["sub_task_path"] == ["A"]
    assert crd["node_kind"] == "agent"
    assert crd["data"]["event"] == "think_chunk"
    assert crd["data"]["content"] == "analyzing A"


def test_roundtrip_node_start_lifecycle_passthrough():
    wrapped = _format(_sub_task(["B"], "agent", {"event": "node_start", "entity_name": "B"}))

    assert wrapped["custom_rsp_data"]["data"] == {"event": "node_start", "entity_name": "B"}


def test_roundtrip_workflow_message_frame():
    wrapped = _format(
        _sub_task(
            ["A", "wf:a"],
            "workflow",
            {"event": "message", "data": {"node_type": "LLM", "text": "parse report"}},
        )
    )

    crd = wrapped["custom_rsp_data"]
    assert crd["node_kind"] == "workflow"
    assert crd["sub_task_path"] == ["A", "wf:a"]
    assert crd["data"] == {
        "event": "message",
        "data": {"node_type": "LLM", "text": "parse report"},
    }


def test_roundtrip_deep_path_preserved_for_tree_building():
    wrapped = _format(
        _sub_task(["A", "X", "wf:z"], "workflow", {"event": "message", "data": {"text": "deep"}})
    )

    assert wrapped["custom_rsp_data"]["sub_task_path"] == ["A", "X", "wf:z"]


def test_sub_task_failed_inner_keeps_outer_success_true():
    wrapped = _format(
        _sub_task(["A", "wf:c"], "workflow", {"event": "node_end", "status": "failed", "error": "timeout"})
    )

    assert wrapped["success"] is True
    assert wrapped["custom_rsp_data"]["data"]["status"] == "failed"


def test_compat_plain_agent_frame_serialization_unchanged():
    wrapped = _format({"type": "think_chunk", "data": {"content": "main agent thinking"}})

    crd = wrapped["custom_rsp_data"]
    assert crd["event"] == "think_chunk"
    assert crd["content"] == "main agent thinking"
    assert "sub_task_path" not in crd


def test_compat_plain_workflow_message_frame_unchanged():
    normalized = EventNormalizer.normalize(
        artifact_event({"event": "message", "data": {"node_type": "QA", "text": "single workflow"}})
    )
    assert normalized == {
        "type": "versatile_proxy",
        "data": {"event": "message", "data": {"node_type": "QA", "text": "single workflow"}},
    }

    wrapped = _format(normalized)
    crd = wrapped["custom_rsp_data"]
    assert crd["event"] == "message"
    assert crd["data"] == {"node_type": "QA", "text": "single workflow"}
    assert "sub_task_path" not in crd


def test_workflow_end_frame_no_longer_suppressed():
    normalized = EventNormalizer.normalize(artifact_event({"event": "end", "data": {}}))
    assert normalized == {"type": "versatile_proxy", "data": {"event": "end", "data": {}}}

    wrapped = _format(normalized)
    assert wrapped["custom_rsp_data"]["event"] == "end"


def test_extract_content_text_part():
    assert EventNormalizer.extract_status_content(status_completed_text("final answer")) == "final answer"


def test_extract_content_data_part_serialized():
    out = EventNormalizer.extract_status_content(status_completed_data({"entity_id": "A", "url": "https://x"}))
    assert json.loads(out) == {"entity_id": "A", "url": "https://x"}


def test_extract_content_empty_message():
    assert EventNormalizer.extract_status_content(status_completed_empty()) == ""


def test_completed_metadata_cascade_result_is_internal_not_display_data():
    metadata = ParseDict(
        {
            "cascade_result": {
                "products": [{"productCode": "P001", "productName": "Stable Fund"}],
                "bankCardNumber": "6605",
            }
        },
        Struct(),
    )
    event = TaskStatusUpdateEvent(status=TaskStatus(state=TASK_STATE_COMPLETED))
    event.metadata.CopyFrom(metadata)

    normalized = EventNormalizer.normalize(event)
    assert normalized is None
