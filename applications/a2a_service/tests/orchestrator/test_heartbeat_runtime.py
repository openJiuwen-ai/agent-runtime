# coding: utf-8
from __future__ import annotations

import asyncio

import pytest
from a2a.server.events import EventQueue
from google.protobuf.json_format import MessageToDict

from orchestrator.heartbeat_runtime import HeartbeatRuntimeManager


def _drain_queue(event_queue: EventQueue) -> list:
    out = []
    inner = getattr(event_queue, "_queue", None) or getattr(event_queue, "queue", None)
    if inner is None:
        return out
    try:
        while True:
            out.append(inner.get_nowait())
    except asyncio.QueueEmpty:
        return out


def _artifact_data(event) -> dict:
    artifact = getattr(event, "artifact", None)
    if artifact is None:
        return {}
    for part in artifact.parts:
        if part.WhichOneof("content") == "data":
            data = MessageToDict(part.data)
            return data if isinstance(data, dict) else {}
    return {}


@pytest.mark.asyncio
async def test_end_only_forwarded_once():
    queue = EventQueue()
    hb = HeartbeatRuntimeManager(
        conv_id="conv-1",
        task_id="task-1",
        event_queue=queue,
        interval_seconds=30,
        timeout_seconds=1800,
    )

    await hb.start_heartbeat("conv-1")
    first = await hb.stop_heartbeat("conv-1", mark_end=True)
    second = await hb.stop_heartbeat("conv-1", mark_end=True)

    assert first["forward_to_frontend"] is True
    assert second["forward_to_frontend"] is False


@pytest.mark.asyncio
async def test_notify_heartbeat_sets_seq_monotonic():
    queue = EventQueue()
    hb = HeartbeatRuntimeManager(
        conv_id="conv-2",
        task_id="task-2",
        event_queue=queue,
        interval_seconds=30,
        timeout_seconds=1800,
    )

    await hb.notify_heartbeat(
        request_id="conv-2",
        heartbeat_type="normal",
        status="processing",
        source="a2a_service",
    )
    await hb.notify_heartbeat(
        request_id="conv-2",
        heartbeat_type="normal",
        status="processing",
        source="a2a_service",
    )

    events = _drain_queue(queue)
    payloads = [_artifact_data(e) for e in events]
    seqs = [p.get("seq") for p in payloads if p.get("type") == "heartbeat"]
    assert seqs == [1, 2]


@pytest.mark.asyncio
async def test_attach_seq_fills_missing_seq():
    queue = EventQueue()
    hb = HeartbeatRuntimeManager(
        conv_id="conv-3",
        task_id="task-3",
        event_queue=queue,
    )

    raw = {
        "type": "heartbeat",
        "data": {
            "request_id": "conv-3",
            "heartbeat_type": "initial",
            "status": "processing",
        },
    }
    await hb.attach_seq(raw, request_id="conv-3")
    assert raw["data"]["seq"] == 1


@pytest.mark.asyncio
async def test_timeout_loop_emits_end_timeout_once():
    queue = EventQueue()
    hb = HeartbeatRuntimeManager(
        conv_id="conv-4",
        task_id="task-4",
        event_queue=queue,
        interval_seconds=1,
        timeout_seconds=1,
    )

    await hb.start_heartbeat("conv-4")
    await asyncio.sleep(1.2)

    events = _drain_queue(queue)
    payloads = [_artifact_data(e) for e in events]
    timeout_ends = []
    for payload in payloads:
        if payload.get("type") != "heartbeat":
            continue
        if payload.get("heartbeat_type") != "end":
            continue
        if payload.get("status") != "timeout":
            continue
        timeout_ends.append(payload)
    assert len(timeout_ends) <= 1
    await hb.cleanup()
