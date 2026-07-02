# coding: utf-8
from __future__ import annotations

from channels.mobile_bank_channel import MobileBankChannel


def test_sub_task_agent_frame_formats_as_sub_task_envelope():
    channel = MobileBankChannel()

    wrapped = channel.format_event(
        {
            "type": "sub_task",
            "data": {
                "sub_task_path": ["A"],
                "node_kind": "agent",
                "data": {"type": "think_chunk", "content": "analyzing A"},
            },
        },
        agent_id="ag",
        conversation_id="conv",
        elapsed=0.01,
    )

    assert wrapped is not None
    crd = wrapped["custom_rsp_data"]
    assert crd["event"] == "sub_task"
    assert crd["sub_task_path"] == ["A"]
    assert crd["node_kind"] == "agent"
    assert crd["data"]["event"] == "think_chunk"
    assert crd["data"]["content"] == "analyzing A"


def test_sub_task_lifecycle_frame_preserves_inner_payload_and_outer_success():
    channel = MobileBankChannel()

    wrapped = channel.format_event(
        {
            "type": "sub_task",
            "data": {
                "sub_task_path": ["A", "wf:c"],
                "node_kind": "workflow",
                "data": {"event": "node_end", "status": "failed", "error": "timeout"},
            },
        },
        agent_id="ag",
        conversation_id="conv",
        elapsed=0.01,
    )

    assert wrapped is not None
    assert wrapped["success"] is True
    crd = wrapped["custom_rsp_data"]
    assert crd["node_kind"] == "workflow"
    assert crd["sub_task_path"] == ["A", "wf:c"]
    assert crd["data"] == {"event": "node_end", "status": "failed", "error": "timeout"}
