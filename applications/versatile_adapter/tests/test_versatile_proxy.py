"""Unit tests for adapter.versatile_proxy.

Drives Solution A: proxy must peel the upstream Versatile envelope so
downstream receives flat {event, data} frames instead of a double-nested
custom_rsp_data.
"""
from __future__ import annotations

import pytest

from adapter.versatile_proxy import VersatileProxy, _unwrap_upstream_frame


def test_unwrap_upstream_frame_message_yields_event_and_data():
    """A Versatile 'message' frame is peeled to {event: 'message', data: {...}}."""
    upstream = {
        "success": True,
        "agent_id": "a-1",
        "conversation_id": "c-1",
        "custom_rsp_data": {
            "event": "message",
            "data": {
                "node_type": "QA",
                "node_name": "xxx",
                "text": "hi",
            },
        },
    }
    assert _unwrap_upstream_frame(upstream) == {
        "event": "message",
        "data": {
            "node_type": "QA",
            "node_name": "xxx",
            "text": "hi",
        },
    }


def test_unwrap_upstream_frame_end_yields_empty_data():
    """A Versatile 'end' frame has no data — peel to {event: 'end', data: {}}."""
    upstream = {
        "success": True,
        "agent_id": "a-1",
        "custom_rsp_data": {"event": "end"},
    }
    assert _unwrap_upstream_frame(upstream) == {"event": "end", "data": {}}


def test_unwrap_upstream_frame_missing_custom_rsp_data_returns_safe_default():
    """Malformed upstream without custom_rsp_data falls back to a 'message'
    frame carrying the raw payload, so downstream can still reason about it."""
    upstream = {"success": True, "garbled": "payload"}
    result = _unwrap_upstream_frame(upstream)
    assert result["event"] == "message"
    assert result["data"] == upstream


def test_unwrap_upstream_frame_non_dict_returns_safe_default():
    """Defensive: if upstream line isn't even a dict, return a safe empty frame."""
    assert _unwrap_upstream_frame(None) == {"event": "message", "data": {}}  # type: ignore[arg-type]
    assert _unwrap_upstream_frame([1, 2, 3]) == {"event": "message", "data": {}}  # type: ignore[arg-type]


def test_unwrap_upstream_frame_already_unwrapped_message_passes_through():
    """Some upstream gateways send {event, data} directly without the custom_rsp_data
    envelope. Peel should pass through unchanged (don't double-wrap)."""
    already_unwrapped = {
        "event": "message",
        "data": {"node_type": "QA", "node_name": "xxx", "text": "hi"},
    }
    assert _unwrap_upstream_frame(already_unwrapped) == already_unwrapped


def test_unwrap_upstream_frame_already_unwrapped_end_passes_through():
    """Already-unwrapped end frame."""
    already_unwrapped_end = {"event": "end", "data": {"node_type": "End"}}
    assert _unwrap_upstream_frame(already_unwrapped_end) == already_unwrapped_end


def test_unwrap_upstream_frame_already_unwrapped_end_without_data():
    """Already-unwrapped end frame with no data key → data defaults to {}."""
    assert _unwrap_upstream_frame({"event": "end"}) == {"event": "end", "data": {}}


# ════════════════════════════════════════════════════════════════════
# dispatch_stream —— 端到端形状
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dispatch_stream_yields_unwrapped_frames_only(httpx_mock):
    """从 httpx SSE 原始行到 yield 出的 chunk：只能是 {event, data}，无外层 envelope。"""
    sse_body = (
        'data: {"success":true,"agent_id":"a","conversation_id":"c",'
        '"custom_rsp_data":{"event":"message","data":{"node_type":"QA","text":"hi"}}}\n\n'
        'data: {"success":true,"custom_rsp_data":{"event":"end"}}\n\n'
    )
    httpx_mock.add_response(
        url="https://va.test/v1/conv-1", method="POST",
        content=sse_body.encode("utf-8"),
        headers={"Content-Type": "text/event-stream"},
    )

    proxy = VersatileProxy(url_template="https://va.test/v1/{conversation_id}")
    chunks = [c async for c in proxy.dispatch_stream(
        body={"custom_data": {}},
        conv_id="conv-1",
    )]

    assert chunks == [
        {"event": "message", "data": {"node_type": "QA", "text": "hi"}},
        {"event": "end", "data": {}},
    ]
    # 明确不允许出现任何外层 envelope 字段
    for chunk in chunks:
        assert set(chunk.keys()) == {"event", "data"}
