# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import pytest

from channels.mobile_bank_channel import MobileBankChannel


def test_parse_request_extracts_query_trace_and_builds_message():
    channel = MobileBankChannel()
    parsed = channel.parse_request(
        {
            "agent_id": "body-agent",
            "input": {"query": "hello"},
            "stream": False,
            "trace_id": "body-trace",
        },
        path_params={"conversation_id": "conv-1", "agent_id": "path-agent"},
        headers={"x-request-id": "header-trace"},
        params={"debug": "1"},
    )

    assert parsed.conversation_id == "conv-1"
    assert parsed.agent_id == "path-agent"
    assert parsed.query == "hello"
    assert parsed.stream is False
    assert parsed.trace_id == "body-trace"

    request = channel.build_message(parsed)
    assert request.message.context_id == "conv-1"
    assert request.message.parts[0].text == "hello"
    assert len(request.message.parts) == 2


def test_parse_request_validation_and_fallback_query_trace():
    channel = MobileBankChannel()
    with pytest.raises(ValueError):
        channel.parse_request([], path_params={})
    with pytest.raises(ValueError):
        channel.parse_request({"agent_id": "a"}, path_params={})
    with pytest.raises(ValueError):
        channel.parse_request({"conversation_id": "c"}, path_params={})

    parsed = channel.parse_request(
        {
            "agent_id": "a",
            "conversation_id": "c",
            "custom_data": {"inputs": {"query": "from-custom"}},
        },
        path_params={},
        headers={"X-TRACE-ID": "upper-header"},
    )
    assert parsed.query == "from-custom"
    assert parsed.trace_id == "upper-header"


def test_format_event_special_event_types():
    channel = MobileBankChannel()

    assert channel.format_event({"data": {}}, agent_id="a", conversation_id="c", elapsed=0.1) is None
    assert channel.format_event({"type": "x", "data": "bad"}, agent_id="a", conversation_id="c", elapsed=0.1) is None
    assert channel.format_event(
        {"type": "versatile_proxy", "data": {"data": {}}},
        agent_id="a",
        conversation_id="c",
        elapsed=0.1,
    ) is None

    workflow = channel.format_event(
        {"type": "versatile_proxy", "data": {"event": "message", "data": {"node": "n"}}},
        agent_id="a",
        conversation_id="c",
        elapsed=0.1,
    )
    assert workflow["custom_rsp_data"]["event"] == "message"

    completed = channel.format_event(
        {"type": "completed", "data": {"content": "done", "cascade_result": {"x": 1}}},
        agent_id="a",
        conversation_id="c",
        elapsed=0.1,
    )
    assert completed is None

    failed = channel.format_event(
        {"type": "failed", "data": {"error": "boom"}},
        agent_id="a",
        conversation_id="c",
        elapsed=0.1,
    )
    assert failed["success"] is False
    assert failed["custom_rsp_data"]["event"] == "interrupt_start"

    input_required = channel.format_event(
        {"type": "input_required", "data": {"content": "need input"}},
        agent_id="a",
        conversation_id="c",
        elapsed=0.1,
    )
    assert input_required["success"] is True


def test_format_event_subtask_and_default_payloads():
    channel = MobileBankChannel()

    sub_task = channel.format_event(
        {
            "type": "sub_task",
            "data": {
                "sub_task_path": ["A", 2],
                "node_kind": "workflow",
                "data": {"event": "message", "data": {"text": "hi"}},
            },
        },
        agent_id="a",
        conversation_id="c",
        elapsed=0.1,
    )
    assert sub_task["custom_rsp_data"]["node_kind"] == "workflow"
    assert sub_task["custom_rsp_data"]["sub_task_path"] == ["A", "2"]

    nested_agent = channel.format_event(
        {"type": "tool_end", "data": {"content": "ok", "plugin": "tool", "data": {"result": 1}}},
        agent_id="a",
        conversation_id="c",
        elapsed=0.1,
    )
    assert nested_agent["custom_rsp_data"]["plugin"] == "tool"
    assert nested_agent["custom_rsp_data"]["data"] == {"result": 1}


def test_extract_inner_meta_branches():
    channel = MobileBankChannel()

    assert channel._extract_inner_meta("bad", node_kind="agent")["type"] == "thought"
    assert channel._extract_inner_meta({"event": "node_start", "x": 1}, node_kind="workflow")["kind"] == "lifecycle"
    workflow = channel._extract_inner_meta({"event": "message", "data": {"x": 1}}, node_kind="workflow")
    assert workflow == {"kind": "workflow", "type": "message", "data": {"x": 1}}
    agent = channel._extract_inner_meta(
        {"type": "tool_end", "content": "ok", "plugin": "p", "display": "d"},
        node_kind="agent",
    )
    assert agent["plugin"] == "p"
    assert agent["display"] == "d"
