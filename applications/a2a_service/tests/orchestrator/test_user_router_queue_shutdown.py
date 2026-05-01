# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
回归测试：a2a EventQueue 正常关闭时的异常分类。

历史 bug：
    user_router 早期版本用 ``type(e).__name__ != "QueueShutDown"`` 字符串
    判断队列关闭信号。a2a-sdk 在 py3.11/3.12 下回退到 ``aiologic.AsyncQueueShutDown``，
    虽然以 ``as QueueShutDown`` 别名导入，但类的 ``__name__`` 仍是
    ``AsyncQueueShutDown``——字符串比较一直为 True，导致每条流式响应都被
    误打 WARNING 并把 ``status_message`` 标成失败。

修复后语义：
    - 队列正常关闭 → 静默退出，无 WARNING、status 不被污染
    - 其他异常 → 上抛由调用方记 WARNING、置 status_message=1
"""
from __future__ import annotations

import pytest
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    ROLE_AGENT,
    TASK_STATE_COMPLETED,
    Message,
    Part,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from loguru import logger

from orchestrator.sse_helpers import log_outbound_sse, next_sse_event


CONV_ID = "90d40c85-cca4-43fe-8e3f-9ad3717fb1b4"
TASK_ID = "task-queue-shutdown"


def _make_event() -> TaskStatusUpdateEvent:
    text_part = Part()
    text_part.text = "hello"
    msg = Message(role=ROLE_AGENT, message_id="m-1")
    msg.parts.extend([text_part])
    return TaskStatusUpdateEvent(
        task_id=TASK_ID,
        context_id=CONV_ID,
        status=TaskStatus(state=TASK_STATE_COMPLETED, message=msg),
    )


@pytest.mark.asyncio
async def test_returns_event_when_queue_has_items():
    queue = EventQueue()
    event = _make_event()
    await queue.enqueue_event(event)

    result = await next_sse_event(queue)

    assert isinstance(result, TaskStatusUpdateEvent)
    assert result.task_id == TASK_ID


@pytest.mark.asyncio
async def test_returns_none_when_queue_closed_empty():
    """py3.11 下队列关闭抛 aiologic.AsyncQueueShutDown，必须被识别为正常结束。

    此测试就是历史 bug 的回归：旧实现用 type(e).__name__ != "QueueShutDown"
    判断，会把 AsyncQueueShutDown 当作"序列化异常"并触发 WARNING。
    """
    queue = EventQueue()
    await queue.close()

    result = await next_sse_event(queue)

    assert result is None


@pytest.mark.asyncio
async def test_drains_pending_events_then_returns_none():
    """先把事件取走（task_done），再 close，下一次取应当返回 None。

    EventQueue.close() 内部会 ``await queue.join()`` 等所有 task_done 完成，
    所以测试顺序必须 enqueue → dequeue → close → dequeue。
    """
    queue = EventQueue()
    event = _make_event()
    await queue.enqueue_event(event)

    first = await next_sse_event(queue)
    await queue.close()
    second = await next_sse_event(queue)

    assert isinstance(first, TaskStatusUpdateEvent)
    assert second is None


@pytest.mark.asyncio
async def test_propagates_unexpected_exception():
    """非 QueueShutDown 的异常必须上抛，由调用方记 WARNING。"""

    class _BoomQueue:
        async def dequeue_event(self):
            raise RuntimeError("boom")

        def task_done(self):  # 不应被调用，dequeue 已抛错
            raise AssertionError("task_done should not be called on failure")

    with pytest.raises(RuntimeError, match="boom"):
        await next_sse_event(_BoomQueue())


# ════════════════════════════════════════════════════════════════════
# 北向 SSE 推送埋点（INFO 级）
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def loguru_records():
    captured: list = []
    handler_id = logger.add(
        lambda msg: captured.append(msg.record),
        level="DEBUG",
        format="{message}",
    )
    try:
        yield captured
    finally:
        logger.remove(handler_id)


def test_log_outbound_sse_emits_info_record(loguru_records):
    log_outbound_sse(
        conversation_id="conv-c1",
        sequence=3,
        payload='{"hello":"world"}',
        event_kind="TaskArtifactUpdateEvent",
    )

    info = [r for r in loguru_records if r["level"].name == "INFO"]
    assert info, "expected at least one INFO record"
    msg = info[-1]["message"]
    assert "→ SSE" in msg
    assert "conv=conv-c1" in msg
    assert "#3" in msg
    assert "kind=TaskArtifactUpdateEvent" in msg
    expected_bytes = len('{"hello":"world"}'.encode("utf-8"))
    assert f"bytes={expected_bytes}" in msg


def test_log_outbound_sse_payload_byte_length_handles_multibyte(loguru_records):
    payload = '{"text":"你好"}'
    log_outbound_sse(
        conversation_id="c2",
        sequence=1,
        payload=payload,
        event_kind="X",
    )
    info = [r for r in loguru_records if r["level"].name == "INFO"]
    assert info
    expected_bytes = len(payload.encode("utf-8"))
    assert f"bytes={expected_bytes}" in info[-1]["message"]


def test_log_outbound_sse_includes_full_payload(loguru_records):
    """SSE 出栈日志在 DEBUG 级别下应把整段 payload 原文打出来，便于现场排障对照前端帧形态。

    生产 INFO 级别保持轻量（仅 conv/#/kind/bytes 元数据），避免每帧 payload 全量
    落盘把日志刷爆；DEBUG 级别补一条 payload 明细行，通过 ``#sequence`` 与
    INFO 行关联。开发联调或问题复盘时把 log_level 调到 DEBUG 即可看到完整报文。
    """
    payload = '{"custom_rsp_data":{"event":"message","data":{"node_type":"Start"}}}'
    log_outbound_sse(
        conversation_id="conv-x",
        sequence=7,
        payload=payload,
        event_kind="TaskArtifactUpdateEvent",
    )

    # INFO 行不应携带 payload 原文，避免生产日志被 SSE 报文刷爆
    info = [r for r in loguru_records if r["level"].name == "INFO"]
    assert info, "expected at least one INFO record"
    info_msg = info[-1]["message"]
    assert f"payload={payload}" not in info_msg, (
        f"INFO message should NOT contain full payload (keep INFO lightweight); "
        f"got: {info_msg!r}"
    )

    # DEBUG 行通过 #sequence 关联到对应 INFO 行，并携带完整 payload
    debug = [r for r in loguru_records if r["level"].name == "DEBUG"]
    assert debug, "expected at least one DEBUG record"
    debug_msg = debug[-1]["message"]
    assert "#7" in debug_msg, (
        f"DEBUG message should carry sequence marker for correlation; got: {debug_msg!r}"
    )
    assert f"payload={payload}" in debug_msg, (
        f"DEBUG message should contain full payload; got: {debug_msg!r}"
    )
