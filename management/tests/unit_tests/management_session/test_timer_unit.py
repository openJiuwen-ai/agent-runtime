# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import asyncio

import pytest

from openjiuwen_runtime.management.session.timer import Timer


@pytest.mark.asyncio
async def test_timer_fires() -> None:
    t = Timer()
    ev = asyncio.Event()

    async def _cb() -> None:
        ev.set()

    await t.start_timer("k", 0, _cb)
    await asyncio.wait_for(ev.wait(), timeout=2.0)
    await t.stop_all()


@pytest.mark.asyncio
async def test_cancel_timer() -> None:
    t = Timer()
    ev = asyncio.Event()

    await t.start_timer("x", 10, ev.set)
    assert await t.cancel_timer("x")
    await t.stop_all()
