# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Session 包系统测试：Access → ServiceManager → ServiceHandler；Mock 部署与消息通道。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Tuple, cast
from unittest.mock import AsyncMock

import pytest

from openjiuwen_runtime.management.session.access import Access
from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
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
from openjiuwen_runtime.management.session.strategies.per_chat_bot import (
    PerChatBotStrategy,
)
from openjiuwen_runtime.management.session.timer import Timer


@dataclass
class TRequest:
    request_id: Optional[str] = "r1"
    chat_id: Optional[str] = "c1"
    bot_id: Optional[str] = "b1"
    user_id: Optional[str] = "u1"
    session_id: Optional[str] = None


def ireq(r: TRequest) -> IRequest:
    return cast(IRequest, r)


class DictStreamParser(IResponseParser):
    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        return data.get("request_id")

    def is_completed(self, data: dict[str, Any]) -> bool:
        if "error_code" in data or data.get("completed") is True:
            return True
        return data.get("done", False) is True

    def response(self, data: dict[str, Any]) -> Any:
        if "message" in data and "error_code" in data:
            return data["message"]
        return data.get("text", data)


class MockMessageChannel:
    """默认立即完成，记录 send 调用。"""

    def __init__(self) -> None:
        self.send_calls: List[Tuple[str, str, Optional[str]]] = []

    async def send(
            self,
            service_id: str,
            wrapper: SessionRequestWrapper,
            *,
            response_parser: IResponseParser,
            on_request_complete: Callable[[Optional[str]], Awaitable[None]],
    ) -> None:
        sreq = wrapper.session_request
        self.send_calls.append((service_id, sreq.session_id, sreq.request_id))
        if wrapper.cancel.done():
            await on_request_complete(sreq.request_id)
            return
        rid = sreq.request_id
        await wrapper.response_queue.put(
            {
                "text": f"ack:{sreq.request_id}",
                "request_id": rid,
                "completed": True,
            }
        )
        await on_request_complete(rid)


class RecordingK8s:
    """可注入 ServiceHandler 的 IDeployController 替身。"""

    def __init__(self) -> None:
        self.deploy_count = 0
        self.delete_count = 0
        self._name: Optional[str] = None

    @property
    def resource_id(self) -> Optional[str]:
        return self._name

    async def deploy(self) -> object:
        self.deploy_count += 1
        self._name = f"pod-{self.deploy_count}"
        return object()

    async def delete(self) -> str:
        self.delete_count += 1
        n = self._name or "unknown"
        self._name = None
        return n


def make_factory(
        channel: Any,
        service_concurrency: int,
        k8s_per_service: bool = False,
) -> Tuple[IServiceInstanceFactory, List[RecordingK8s]]:
    k8s_list: list[RecordingK8s] = []

    class _F(IServiceInstanceFactory):
        async def new_service(self, response_parser: IResponseParser) -> IServiceHandler:
            if k8s_per_service:
                k8s = RecordingK8s()
                k8s_list.append(k8s)
                deploy = k8s
            else:
                deploy = NoOpDeployController()
            return ServiceHandler(
                total_concurrency=service_concurrency,
                message_channel=channel,
                response_parser=response_parser,
                deploy_controller=deploy,
            )

    return _F(), k8s_list


async def _build_access(
        *,
        service_concurrency: int = 10,
        per_session: int = 1,
        min_idle: int = 0,
        max_services: int = 5,
        k8s_per_service: bool = False,
        channel: Any = None,
) -> Tuple[Access, Any, ServiceManager, List[RecordingK8s]]:
    ch = channel or MockMessageChannel()
    factory, k8s_list = make_factory(ch, service_concurrency, k8s_per_service)
    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(100, 1000)
    sm = ServiceManager(
        service_factory=factory,
        dual_queue=dq,
        timer=Timer(),
        service_concurrency=service_concurrency,
        min_idle_services=min_idle,
        max_services=max_services,
        autoscale_interval=0.2,
        service_idle_ttl=0,
    )
    acc = Access(sm)
    cfg = AccessConfig(
        user_queue_size=1000,
        system_queue_size=100,
        service_concurrency=service_concurrency,
        min_idle_services=min_idle,
        max_services=max_services,
    )
    await acc.init(
        response_parser=DictStreamParser(),
        config=cfg,
        session_config=SessionConfig(concurrency=per_session, ttl=0),
        strategy=PerChatBotStrategy(),
    )
    return acc, ch, sm, k8s_list


@pytest.mark.asyncio
async def test_session_affinity_same_service() -> None:
    acc, channel, sm, _ = await _build_access(
        service_concurrency=10, per_session=1, min_idle=0, max_services=2
    )
    try:
        skey = "c1::b1"
        t1 = TRequest(request_id="a", chat_id="c1", bot_id="b1")
        t2 = TRequest(request_id="b", chat_id="c1", bot_id="b1")
        r1, r2 = None, None
        async for x in acc.send_message(ireq(t1)):
            r1 = x
        async for x in acc.send_message(ireq(t2)):
            r2 = x
        assert r1 and r2
        assert len(channel.send_calls) == 2
        assert channel.send_calls[0][0] == channel.send_calls[1][0]
        assert channel.send_calls[0][1] == skey
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_bootstrap_creates_min_idle_services() -> None:
    acc, ch, sm, k8s_list = await _build_access(
        service_concurrency=2,
        per_session=1,
        min_idle=2,
        max_services=5,
        k8s_per_service=True,
    )
    try:
        assert len(k8s_list) == 2
        assert {k.deploy_count for k in k8s_list} == {1}
        async for _ in acc.send_message(
                ireq(TRequest(request_id="1", chat_id="a", bot_id="b"))
        ):
            pass
        assert len(ch.send_calls) >= 1
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_routing_fails_when_no_instance_available() -> None:
    """无可用服务实例时返回 100001（与单条消息循环的串行模型一致，用 mock 覆盖失败路径）。"""
    acc, _, sm, _ = await _build_access(
        service_concurrency=1,
        per_session=1,
        min_idle=0,
        max_services=1,
    )
    try:
        sm._pick_or_create = AsyncMock(return_value=None)  # type: ignore[method-assign]
        out: list[Any] = []
        async for x in acc.send_message(
                ireq(TRequest(request_id="1", chat_id="a", bot_id="b"))
        ):
            out.append(x)
        assert out
        s = str(out[0])
        assert "并发" in s or "100001" in s
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_mock_k8s_per_service_instance() -> None:
    """k8s_per_service 时每个拉起的实例有独立 IDeploy；不同 session 在容量足够时共用一个。"""
    acc, ch, sm, k8s_list = await _build_access(
        service_concurrency=6,
        per_session=2,
        min_idle=0,
        max_services=3,
        k8s_per_service=True,
    )
    try:
        async for _ in acc.send_message(
                ireq(TRequest(request_id="1", chat_id="a", bot_id="b"))
        ):
            pass
        async for _ in acc.send_message(
                ireq(TRequest(request_id="2", chat_id="b", bot_id="b"))
        ):
            pass
        assert len(k8s_list) == 1
        assert k8s_list[0].deploy_count == 1
        assert ch.send_calls[0][0] == ch.send_calls[1][0]
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_k8s_delete_called_on_service_handler() -> None:
    channel = MockMessageChannel()
    k8s = RecordingK8s()
    p = DictStreamParser()
    h = ServiceHandler(
        total_concurrency=10,
        message_channel=channel,
        response_parser=p,
        deploy_controller=k8s,
    )
    await h.deploy()
    assert k8s.deploy_count == 1
    await h.delete()
    assert k8s.delete_count == 1


@pytest.mark.asyncio
async def test_k8s_deploy_mock_async() -> None:
    m = AsyncMock()
    m.deploy = AsyncMock()
    m.delete = AsyncMock()
    m.resource_id = None

    class MFactory(IServiceInstanceFactory):
        async def new_service(
                self, response_parser: IResponseParser
        ) -> IServiceHandler:
            return ServiceHandler(
                total_concurrency=3,
                message_channel=MockMessageChannel(),
                response_parser=response_parser,
                deploy_controller=m,
            )

    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(5, 5)
    sm = ServiceManager(
        MFactory(),
        dq,
        Timer(),
        service_concurrency=3,
        min_idle_services=0,
        max_services=1,
        service_idle_ttl=0,
    )
    await sm.init(DictStreamParser())
    h = await sm._new_deployed()  # noqa: SLF001
    assert m.deploy.await_count == 1
    if h is not None:
        await h.delete()
    await sm.stop()
