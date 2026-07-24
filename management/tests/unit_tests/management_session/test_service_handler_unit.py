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
    ScopeRequestWrapper,
)
from openjiuwen_runtime.management.session.router import SessionRouter
from openjiuwen_runtime.management.session.runtime import NoOpDeployController
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.service_scope_handler import ServiceScopeHandler
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
        wrapper: ScopeRequestWrapper,
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
        service_id="s1",
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
        service_template=None,
    )
    # quota 预留：解耦后由 ServiceHandler.try_reserve_session_quota 承担
    assert h.available_concurrency == 1
    assert h.try_reserve_session_quota("s1", 1)
    assert h.available_concurrency == 0
    # 消息经 ServiceScopeHandler（持有 [h] 作为 endpoint）路由到 ServiceHandler.send_message
    sh = ServiceScopeHandler("s1", 1, [h], SessionRouter())
    w = ScopeRequestWrapper(
        _sreq(), asyncio.Queue(), asyncio.get_running_loop().create_future()
    )
    await sh.handle_message(w)
    assert h.inflight_requests == 0
    # evict_session 释放 quota（pod-local）
    await h.evict_session("s1")
    assert h.available_concurrency == 1


@pytest.mark.asyncio
async def test_dispatch_inbound_unknown() -> None:
    ch = _Ch()
    p = _P()
    h = ServiceHandler(
        total_concurrency=2,
        message_channel=ch,
        response_parser=p,
        deploy_controller=NoOpDeployController(),
        service_template=None,
    )
    assert not await h.dispatch_inbound_chunk({"request_id": "nope"}, p)
