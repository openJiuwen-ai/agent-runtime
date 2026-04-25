# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""自 Access 入口的系统级测试；K8s/消息通道全部 Mock，不依赖外网与集群。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, cast

import pytest

from openjiuwen_runtime.management.session.access import Access
from openjiuwen_runtime.management.session.docker_service_handler import DockerServiceHandler, DockerDeployController
from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
from openjiuwen_runtime.management.session.internal_events import ServiceReclaimEvent
from openjiuwen_runtime.management.session.interfaces import (
    IResponseParser,
    IServiceInstanceFactory,
    IServiceHandler,
    IRequest,
    SessionRequestWrapper,
)
from openjiuwen_runtime.management.session.models import AccessConfig, SessionConfig
from openjiuwen_runtime.management.session.runtime import NoOpDeployController
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.service_manager import ServiceManager, QueueItem
from openjiuwen_runtime.management.session.strategies.per_chat_bot import PerChatBotStrategy
from openjiuwen_runtime.management.session.timer import Timer


@dataclass
class TRequest:
    request_id: Optional[str] = "req-1"
    chat_id: Optional[str] = "c1"
    bot_id: Optional[str] = "b1"
    user_id: Optional[str] = "u1"
    session_id: Optional[str] = None


class _P(IResponseParser):
    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        return data.get("request_id")

    def is_completed(self, data: dict[str, Any]) -> bool:
        return bool(data.get("completed") or data.get("error_code") is not None)

    def response(self, data: dict[str, Any]) -> Any:
        if "message" in data and "error_code" in data:
            return data["message"]
        return data.get("result", data)


class MockChannel:
    def __init__(self) -> None:
        self.send_log: List[tuple] = []

    async def send(
        self,
        service_id: str,
        wrapper: SessionRequestWrapper,
        *,
        response_parser: IResponseParser,
        on_request_complete: Callable[[Optional[str]], Awaitable[None]],
    ) -> None:
        self.send_log.append((service_id, wrapper.session_request.session_id))
        if wrapper.cancel.done():
            await on_request_complete(wrapper.session_request.request_id)
            return
        rid = wrapper.session_request.request_id
        await wrapper.response_queue.put(
            {"request_id": rid, "result": "ok", "completed": True}
        )
        await on_request_complete(rid)


class _Rec:
    def __init__(self) -> None:
        self.deploys = 0
        self.deletes = 0
        self._id: Optional[str] = None

    @property
    def resource_id(self) -> Optional[str]:
        return self._id

    async def deploy(self) -> str:
        self.deploys += 1
        self._id = f"r-{self.deploys}"
        return self._id

    async def delete(self) -> str:
        self.deletes += 1
        x = self._id or ""
        self._id = None
        return x


class _Factory(IServiceInstanceFactory):
    def __init__(self, ch: MockChannel, sc: int, dlist: list[Optional[object]]) -> None:
        self._ch = ch
        self._sc = sc
        self._d = dlist

    async def new_service(self, response_parser: IResponseParser) -> IServiceHandler:
        d = self._d.pop(0) if self._d else None
        dc: Any = d if d is not None else NoOpDeployController()
        return ServiceHandler(
            total_concurrency=self._sc,
            message_channel=self._ch,
            response_parser=response_parser,
            deploy_controller=dc,
        )


async def _stack(
    min_idle: int = 0,
    max_svc: int = 5,
    scap: int = 10,
    deploys: int = 0,
) -> tuple[Access, MockChannel, ServiceManager, Optional[_Rec], _Factory]:
    ch = MockChannel()
    drec: Optional[_Rec]
    if deploys > 0:
        drec = _Rec()
        dlist: list[Optional[object]] = [drec] * 20
    else:
        drec = None
        dlist = [None] * 20
    f = _Factory(ch, scap, dlist)
    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(100, 1000)
    sm = ServiceManager(
        f,
        dq,
        Timer(),
        service_concurrency=scap,
        min_idle_services=min_idle,
        max_services=max_svc,
        autoscale_interval=0.1,
        service_idle_ttl=300,
    )
    acc = Access(sm)
    cfg = AccessConfig(
        user_queue_size=1000,
        system_queue_size=100,
        service_concurrency=scap,
        min_idle_services=min_idle,
        max_services=max_svc,
    )
    await acc.init(
        response_parser=_P(),
        strategy=PerChatBotStrategy(),
        config=cfg,
        session_config=SessionConfig(concurrency=2, ttl=0),
    )
    return acc, ch, sm, drec, f


@pytest.mark.asyncio
async def test_access_end_to_end_affinity() -> None:
    acc, ch, sm, _, _ = await _stack()
    try:
        skey = "c1::b1"
        out: list[Any] = []
        async for x in acc.send_message(cast(IRequest, TRequest(request_id="a"))):
            out.append(x)
        async for x in acc.send_message(cast(IRequest, TRequest(request_id="b"))):
            out.append(x)
        assert len(ch.send_log) == 2
        assert ch.send_log[0][0] == ch.send_log[1][0]
        assert ch.send_log[0][1] == skey
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_docker_controller_stub() -> None:
    d = DockerServiceHandler(image="x:y", host="127.0.0.1", publish_port=9000)
    c = DockerDeployController(d)
    r = await c.deploy()
    assert r.port == 9000
    assert await c.delete()


@pytest.mark.asyncio
async def test_k8s_mock_deploy_count() -> None:
    acc, _, sm, drec, _ = await _stack(min_idle=1, deploys=1)
    try:
        assert drec is not None
        assert drec.deploys >= 1
        async for _ in acc.send_message(cast(IRequest, TRequest(request_id="z"))):
            pass
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_system_queue_reclaim() -> None:
    _, _, sm, _, _ = await _stack()
    try:
        await sm.enqueue_system(ServiceReclaimEvent(service_id="nope"))
        await asyncio.sleep(0.01)
    finally:
        await sm.stop()
