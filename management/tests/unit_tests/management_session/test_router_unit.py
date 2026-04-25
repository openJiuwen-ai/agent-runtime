# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import pytest

from openjiuwen_runtime.management.session.router import ServiceRouter, SessionRouter


@pytest.mark.asyncio
async def test_session_router() -> None:
    r = SessionRouter()
    await r.set_request_session("r1", "s1")
    assert await r.get_request_session("r1") == "s1"
    assert await r.delete_request_session("r1")
    assert await r.get_request_session("r1") is None


@pytest.mark.asyncio
async def test_session_router_clear() -> None:
    r = SessionRouter()
    await r.set_request_session("a", "s")
    await r.set_request_session("b", "s")
    await r.clear()
    assert await r.get_request_session_size() == 0


@pytest.mark.asyncio
async def test_service_router() -> None:
    r = ServiceRouter()
    await r.set_session_service("sess-1", "svc-a")
    assert await r.get_session_service("sess-1") == "svc-a"
