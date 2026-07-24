# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""同一 ServiceHandler 上多 session 交错并发：session 内限流，不阻塞其他 session。"""

from __future__ import annotations

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


class HoldChannel:
    """记录已进入 send 的请求（session 维），在 gate 释放前保持阻塞，用于观测并发。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.in_send = 0
        self.max_in_send = 0
        self.by_session: dict[str, int] = {}
        self.gate = asyncio.Event()

    async def send(
        self,
        service_id: str,
        wrapper: ScopeRequestWrapper,
        *,
        response_parser: IResponseParser,
        on_request_complete: Callable[[Optional[str]], Awaitable[None]],
    ) -> None:
        sid = wrapper.session_request.service_id
        rid = wrapper.session_request.request_id
        async with self._lock:
            self.in_send += 1
            if self.in_send > self.max_in_send:
                self.max_in_send = self.in_send
            self.by_session[sid] = self.by_session.get(sid, 0) + 1
        await self.gate.wait()
        async with self._lock:
            self.in_send -= 1
            self.by_session[sid] -= 1
        if wrapper.cancel.done():
            await on_request_complete(rid)
            return
        await wrapper.response_queue.put(
            {"request_id": rid, "t": "ok", "done": True, "completed": True}
        )
        await on_request_complete(rid)


def _wrap(
    session_id: str, rid: str, cap: int, loop: asyncio.AbstractEventLoop
) -> ScopeRequestWrapper:
    sreq: ISessionRequest = SessionRequest(
        service_id=session_id,
        concurrency=cap,
        ttl=0,
        request_id=rid,
        raw=cast(IRequest, object()),
    )
    return ScopeRequestWrapper(sreq, asyncio.Queue(), loop.create_future())


@pytest.mark.asyncio
async def test_session_cap_interleaves_other_sessions() -> None:
    """
    11 条 s1、9 条 s2 同时进入：s1 并发度 10，故仅 10 条进入 send；
    第 11 条在 session 层阻塞，s2 的 9 条仍可占满服务并发，共 19 条在 send 内等待 gate。
    """
    ch = HoldChannel()
    p = _P()
    h = ServiceHandler(
        total_concurrency=20,
        message_channel=ch,
        response_parser=p,
        deploy_controller=NoOpDeployController(),
        service_template=None,
    )
    loop = asyncio.get_running_loop()
    cap = 10
    # 解耦后：每 session 一个 ServiceScopeHandler（持有 [h] 作为 endpoint），各自 semaphore(cap)
    router = SessionRouter()
    assert h.try_reserve_session_quota("sess1", cap)
    assert h.try_reserve_session_quota("sess2", cap)
    sh1 = ServiceScopeHandler("sess1", cap, [h], router)
    sh2 = ServiceScopeHandler("sess2", cap, [h], router)
    tasks = []
    for i in range(11):
        w = _wrap("sess1", f"s1-r{i}", cap, loop)
        tasks.append(asyncio.create_task(sh1.handle_message(w)))
    for j in range(9):
        w = _wrap("sess2", f"s2-r{j}", cap, loop)
        tasks.append(asyncio.create_task(sh2.handle_message(w)))
    for _ in range(200):
        if ch.in_send == 19:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError(
            f"in_send={ch.in_send} by_session={ch.by_session} max={ch.max_in_send}"
        )
    assert ch.in_send == 19, ch.in_send
    assert ch.max_in_send == 19
    assert ch.by_session.get("sess1", 0) == 10
    assert ch.by_session.get("sess2", 0) == 9
    ch.gate.set()
    await asyncio.gather(*tasks)
    assert h.inflight_requests == 0
    await h.evict_session("sess1")
    await h.evict_session("sess2")
    assert h.available_concurrency == 20
