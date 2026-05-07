# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, cast

import pytest

from openjiuwen_runtime.management.session.interfaces import (
    IResponseParser,
    IRequest,
    ISessionRequest,
    SessionRequestWrapper,
)
from openjiuwen_runtime.management.session.runtime import NoOpDeployController
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.session_request import SessionRequest


@dataclass
class _P(IResponseParser):
    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        return data.get("request_id")

    def is_completed(self, data: dict[str, Any]) -> bool:
        return bool(data.get("done") or data.get("completed"))

    def response(self, data: dict[str, Any]) -> Any:
        return data.get("t", data)


class _Ch:
    def __init__(self) -> None:
        self.calls = 0

    async def send(
        self,
        service_id: str,
        wrapper: SessionRequestWrapper,
        *,
        response_parser: IResponseParser,
        on_request_complete: Callable[[Optional[str]], Awaitable[None]],
    ) -> None:
        self.calls += 1
        rid = wrapper.session_request.request_id
        if wrapper.cancel.done():
            await on_request_complete(rid)
            return
        await wrapper.response_queue.put(
            {"request_id": rid, "t": "ok", "done": True, "completed": True}
        )
        await on_request_complete(rid)


def _sreq() -> ISessionRequest:
    return SessionRequest(
        session_id="s1",
        concurrency=1,
        ttl=0,
        request_id="r1",
        raw=cast(IRequest, object()),
    )


@pytest.mark.asyncio
async def test_one_inflight_decrements() -> None:
    ch = _Ch()
    p = _P()
    h = ServiceHandler(
        total_concurrency=1,
        message_channel=ch,
        response_parser=p,
        deploy_controller=NoOpDeployController(),
    )
    w = SessionRequestWrapper(
        _sreq(), asyncio.Queue(), asyncio.get_running_loop().create_future()
    )
    assert h.available_concurrency == 1
    await h.handle_message(w)
    assert h.inflight_requests == 0
    assert h.available_concurrency == 0
    await h.remove_session("s1")
    assert h.available_concurrency == 1
    assert await h._session_router.get_request_session_size() == 0


@pytest.mark.asyncio
async def test_dispatch_inbound_unknown() -> None:
    ch = _Ch()
    p = _P()
    h = ServiceHandler(
        total_concurrency=2,
        message_channel=ch,
        response_parser=p,
        deploy_controller=NoOpDeployController(),
    )
    assert not await h.dispatch_inbound_chunk({"request_id": "nope"}, p)
