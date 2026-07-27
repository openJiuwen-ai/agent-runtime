# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""同一 ServiceHandler 上多 chat_session 交错并发：scope 内 semaphore 限流，不阻塞其他 scope。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

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


class _Raw(IRequest):
    """最小 IRequest 实现：携带 chat_session 标识（session_id）。"""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    @property
    def request_id(self) -> Optional[str]:
        return None

    @property
    def chat_id(self) -> Optional[str]:
        return None

    @property
    def user_id(self) -> Optional[str]:
        return None

    @property
    def bot_id(self) -> Optional[str]:
        return None

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id


class HoldChannel:
    """记录已进入 send 的请求，在 gate 释放前保持阻塞，用于观测并发。"""

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
    service_id: str, chat_session_id: str, rid: str, cap: int, loop: asyncio.AbstractEventLoop
) -> ScopeRequestWrapper:
    sreq: ISessionRequest = SessionRequest(
        service_id=service_id,
        concurrency=cap,
        ttl=0,
        request_id=rid,
        raw=_Raw(session_id=chat_session_id),
    )
    return ScopeRequestWrapper(sreq, asyncio.Queue(), loop.create_future())


@pytest.mark.asyncio
async def test_session_cap_interleaves_other_sessions() -> None:
    """
    11 个 chat_session 打到 sh1（并发度 10）、9 个打到 sh2：各自 semaphore(10) 限流，
    sh1 仅 10 个进入 send、第 11 个在 acquire 阻塞；sh2 的 9 个全部进入。共 19 个在 send 等待 gate。
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
    router = SessionRouter()
    assert h.try_reserve_session_quota("sess1", cap)
    assert h.try_reserve_session_quota("sess2", cap)
    sh1 = ServiceScopeHandler("sess1", cap, [h], router, reserve_per_pod=cap)
    sh2 = ServiceScopeHandler("sess2", cap, [h], router, reserve_per_pod=cap)

    async def run(sh: ServiceScopeHandler, service_id: str, chat_session_id: str, rid: str) -> None:
        # chat_session 先绑定到 h，再在 semaphore 保护下发送
        sh.bind(chat_session_id, h.id)
        await sh.acquire()
        try:
            await sh.handle_message(_wrap(service_id, chat_session_id, rid, cap, loop))
        finally:
            sh.release()

    tasks = []
    for i in range(11):
        tasks.append(asyncio.create_task(run(sh1, "sess1", f"sess1-cs{i}", f"s1-r{i}")))
    for j in range(9):
        tasks.append(asyncio.create_task(run(sh2, "sess2", f"sess2-cs{j}", f"s2-r{j}")))
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
