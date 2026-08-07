# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Request cancellation, deadline, cleanup, and bound logging tests."""

import asyncio
import logging

import pytest

from openjiuwen_runtime.service import (
    DeadlineExceeded,
    Envelope,
    Interrupted,
    Metadata,
    SystemContext,
)


def _env(request_id: str = "r1", trace_id: str | None = "t1") -> Envelope:
    return Envelope(
        type="work",
        metadata=Metadata(request_id=request_id, trace_id=trace_id),
        rawdata={},
    )


@pytest.mark.unit
async def test_interrupt_wakes_waiters_and_preserves_first_reason():
    ctx = SystemContext().for_request(_env())
    waiter = asyncio.create_task(ctx.wait_interrupted())

    await asyncio.sleep(0)
    ctx.interrupt("client disconnected")
    ctx.interrupt("later reason")
    await asyncio.wait_for(waiter, timeout=1)

    with pytest.raises(Interrupted, match="client disconnected"):
        ctx.check_interrupted()
    await ctx.close()


@pytest.mark.unit
async def test_deadline_is_absolute_and_wakes_waiters():
    ctx = SystemContext(request_timeout_seconds=0.02).for_request(_env())

    remaining = ctx.remaining_seconds()
    assert remaining is not None and 0 < remaining <= 0.02 + 1e-6
    await asyncio.wait_for(ctx.wait_interrupted(), timeout=1)
    assert ctx.remaining_seconds() == 0
    with pytest.raises(DeadlineExceeded):
        ctx.check_interrupted()
    await ctx.close()


@pytest.mark.unit
async def test_close_is_idempotent_lifo_and_continues_after_failure(caplog):
    logger = logging.getLogger("test.request.cleanup")
    logger.setLevel(logging.ERROR)
    ctx = SystemContext(logger=logger).for_request(_env())
    calls: list[str] = []

    async def first() -> None:
        calls.append("first")

    def failing() -> None:
        calls.append("failing")
        raise RuntimeError("cleanup failed")

    def last() -> None:
        calls.append("last")

    ctx.add_cleanup(first)
    ctx.add_cleanup(failing)
    ctx.add_cleanup(last)

    with caplog.at_level(logging.ERROR, logger=logger.name):
        await asyncio.gather(ctx.close(), ctx.close())
        await ctx.close()

    assert calls == ["last", "failing", "first"]
    assert ctx.closed is True
    assert "request cleanup failed" in caplog.text


@pytest.mark.unit
def test_request_logger_adds_request_and_trace_ids(caplog):
    logger = logging.getLogger("test.request.bound")
    logger.setLevel(logging.INFO)
    ctx = SystemContext(logger=logger).for_request(_env("request-7", "trace-9"))

    with caplog.at_level(logging.INFO, logger=logger.name):
        ctx.logger.info("bound message")

    record = next(record for record in caplog.records if record.message == "bound message")
    assert record.request_id == "request-7"
    assert record.trace_id == "trace-9"
    assert isinstance(ctx.attrs, dict)
    assert ctx.principal is None
