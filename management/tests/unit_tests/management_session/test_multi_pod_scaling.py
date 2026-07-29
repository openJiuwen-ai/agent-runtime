# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Multi-Pod 弹性伸缩单测：reserve_per_pod 计算、chat_session 亲和/路由/扩容触发、端到端扩到多 Pod、单 Pod 不扩容（兼容）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional

import pytest

from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
from openjiuwen_runtime.management.session.interfaces import (
    IResponseParser,
    IRequest,
    IServiceInstanceFactory,
    IServiceHandler,
    ScopeRequestWrapper,
)
from openjiuwen_runtime.management.session.models import MessageType
from openjiuwen_runtime.management.session.router import SessionRouter
from openjiuwen_runtime.management.session.runtime import NoOpDeployController
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.service_manager import ServiceManager, QueueItem
from openjiuwen_runtime.management.session.service_scope_handler import ServiceScopeHandler
from openjiuwen_runtime.management.session.session_request import SessionRequest
from openjiuwen_runtime.management.session.session_runtime_manager import SessionRuntimeManager
from openjiuwen_runtime.management.session.timer import Timer


@dataclass
class _P(IResponseParser):
    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        return data.get("request_id")

    def is_completed(self, data: dict[str, Any]) -> bool:
        return bool(data.get("done") or data.get("completed"))

    def response(self, data: dict[str, Any]) -> Any:
        return data.get("t", data)


class _Raw(IRequest):
    def __init__(self, session_id: str, request_id: str = "r") -> None:
        self._session_id = session_id
        self._request_id = request_id

    @property
    def request_id(self) -> Optional[str]:
        return self._request_id

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


class _HoldCh:
    """send 在 gate 释放前保持阻塞，用于在并发下撑高 inflight 触发扩容。"""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.send_log: List[str] = []  # 每次发送的 endpoint_id（Pod id）

    async def send(
        self,
        service_id: str,
        wrapper: ScopeRequestWrapper,
        *,
        response_parser: IResponseParser,
        on_request_complete: Callable[[Optional[str]], Awaitable[None]],
    ) -> None:
        self.send_log.append(service_id)
        rid = wrapper.session_request.request_id
        await self.gate.wait()
        if wrapper.cancel.done():
            await on_request_complete(rid)
            return
        await wrapper.response_queue.put(
            {"request_id": rid, "t": "ok", "done": True, "completed": True}
        )
        await on_request_complete(rid)


class _Factory(IServiceInstanceFactory):
    """记录每个新建 ServiceHandler，用于断言扩容出的 Pod 数。"""

    def __init__(self, ch: Any, pod_concurrency: int) -> None:
        self._ch = ch
        self._sc = pod_concurrency
        self.handlers: List[ServiceHandler] = []

    async def new_service(
        self, response_parser: IResponseParser, service_template: Optional[dict] = None
    ) -> IServiceHandler:
        h = ServiceHandler(
            total_concurrency=self._sc,
            message_channel=self._ch,
            response_parser=response_parser,
            deploy_controller=NoOpDeployController(),
            service_template=service_template,
        )
        self.handlers.append(h)
        return h


def _sreq(scope_id: str, scope_concurrency: int, chat_session_id: str, rid: str) -> SessionRequest:
    return SessionRequest(
        service_id=scope_id,
        concurrency=scope_concurrency,
        ttl=0,
        request_id=rid,
        raw=_Raw(session_id=chat_session_id, request_id=rid),
    )


# ==================== reserve_per_pod 计算 ====================

def test_reserve_per_pod_min_of_scope_and_pod() -> None:
    """reserve_per_pod = min(scope_concurrency, pod_concurrency)。"""
    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(10, 10)
    # pod_concurrency = service_concurrency = 2（Manager 全局）
    sm = ServiceManager(
        _Factory(_HoldCh(), 2), dq, Timer(),
        service_concurrency=2, min_idle_services=0, max_services=10,
        deploy_mode="subprocess",
    )
    # 多 Pod：scope=4 > pod=2 → reserve_per_pod=2
    assert sm.reserve_per_pod_for(_sreq("s", 4, "cs", "r")) == 2
    # 单 Pod：scope=2 ≤ pod=4 → reserve_per_pod=2（= scope_concurrency）
    sm2 = ServiceManager(
        _Factory(_HoldCh(), 4), dq, Timer(),
        service_concurrency=4, min_idle_services=0, max_services=10,
        deploy_mode="subprocess",
    )
    assert sm2.reserve_per_pod_for(_sreq("s", 2, "cs", "r")) == 2


# ==================== pick_or_bind：亲和 / 首个未满 / 扩容触发 ====================

def test_pick_or_bind_affinity_first_not_full_and_scale_trigger() -> None:
    p = _P()
    h1 = ServiceHandler(total_concurrency=4, message_channel=_HoldCh(), response_parser=p,
                        deploy_controller=NoOpDeployController(), service_template=None)
    h2 = ServiceHandler(total_concurrency=4, message_channel=_HoldCh(), response_parser=p,
                        deploy_controller=NoOpDeployController(), service_template=None)
    sh = ServiceScopeHandler("s1", 4, [], SessionRouter(), reserve_per_pod=2)

    # 无端点 → None（触发扩容）
    assert sh.pick_or_bind("cs1") is None

    # 装入 h1；cs1 → h1（绑定），cs2 → h1（session_count 1 < 2，首个未满）
    sh.add_endpoint(h1)
    assert sh.pick_or_bind("cs1") is h1
    assert sh.pick_or_bind("cs2") is h1
    assert sh.endpoint_session_count(h1.id) == 2

    # h1 已绑定 2 个 chat_session = reserve_per_pod（满）→ cs3 触发扩容（None）
    assert sh.pick_or_bind("cs3") is None

    # 装入 h2；cs3 → h2（首个未满）；cs1 仍亲和 h1（即便 h1 满，粘性优先）
    sh.add_endpoint(h2)
    assert sh.pick_or_bind("cs3") is h2
    assert sh.pick_or_bind("cs1") is h1  # 亲和优先于容量上限


def test_bind_unbind_lifecycle_and_is_empty() -> None:
    p = _P()
    h1 = ServiceHandler(total_concurrency=4, message_channel=_HoldCh(), response_parser=p,
                        deploy_controller=NoOpDeployController(), service_template=None)
    sh = ServiceScopeHandler("s1", 4, [h1], SessionRouter(), reserve_per_pod=2)

    sh.bind("cs1", h1.id)
    sh.bind("cs2", h1.id)
    assert sh.endpoint_session_count(h1.id) == 2
    assert not sh.is_empty()

    assert sh.unbind("cs1") == h1.id
    assert sh.endpoint_session_count(h1.id) == 1
    # 改绑：cs2 从 h1 迁到不存在的端点时旧集合应清理
    sh.bind("cs2", "other-endpoint")
    assert sh.endpoint_session_count(h1.id) == 0

    assert sh.unbind("cs2") == "other-endpoint"
    assert sh.is_empty() is False  # 仍有 h1 端点
    assert sh.remove_endpoint(h1.id) is True
    assert sh.is_empty() is True


# ==================== 端到端：handle_user_request 扩到多 Pod / 单 Pod 不扩 ====================

async def _build_runtime(pod_concurrency: int) -> tuple[SessionRuntimeManager, ServiceManager, _Factory, _HoldCh]:
    ch = _HoldCh()
    factory = _Factory(ch, pod_concurrency)
    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(100, 1000)
    sm = ServiceManager(
        factory, dq, Timer(),
        service_concurrency=pod_concurrency,
        min_idle_services=0,
        max_services=10,
        autoscale_interval=0.2,
        service_idle_ttl=300,
        deploy_mode="subprocess",
    )
    rt = SessionRuntimeManager(Timer(), sm)
    sm.set_session_runtime(rt)
    await sm.init(_P())
    await sm.start()
    return rt, sm, factory, ch


@pytest.mark.asyncio
async def test_multi_pod_scales_to_two_pods() -> None:
    """scope=4, pod=2 → reserve_per_pod=2, max_scope_pods=2；4 个 chat_session 扩到 2 个 Pod。"""
    rt, sm, factory, ch = await _build_runtime(pod_concurrency=2)
    try:
        loop = asyncio.get_running_loop()

        async def one(csid: str, rid: str) -> None:
            from openjiuwen_runtime.management.session.interfaces import RawMessage
            wrapper = ScopeRequestWrapper(_sreq("scope1", 4, csid, rid), asyncio.Queue(), loop.create_future())
            await rt.handle_user_request(RawMessage(MessageType.USER_REQUEST, wrapper))

        tasks = [asyncio.create_task(one(f"cs{i}", f"r{i}")) for i in range(4)]
        # 等待扩到 2 个 Pod（每端点 inflight 撑满 reserve_per_pod=2 后触发第 2 个）
        for _ in range(300):
            if len(factory.handlers) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(factory.handlers) == 2, f"expected 2 pods, got {len(factory.handlers)}"
        # 2 个 Pod 均被使用
        assert len(set(ch.send_log)) == 2
        ch.gate.set()
        await asyncio.gather(*tasks)
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_single_pod_does_not_scale() -> None:
    """scope=2 ≤ pod=4 → max_scope_pods=1；2 个 chat_session 全在 1 个 Pod，不扩容。"""
    rt, sm, factory, ch = await _build_runtime(pod_concurrency=4)
    try:
        loop = asyncio.get_running_loop()

        async def one(csid: str, rid: str) -> None:
            from openjiuwen_runtime.management.session.interfaces import RawMessage
            wrapper = ScopeRequestWrapper(_sreq("scope1", 2, csid, rid), asyncio.Queue(), loop.create_future())
            await rt.handle_user_request(RawMessage(MessageType.USER_REQUEST, wrapper))

        tasks = [asyncio.create_task(one(f"cs{i}", f"r{i}")) for i in range(2)]
        for _ in range(200):
            if ch.send_log and len(set(ch.send_log)) >= 1 and all(t.done() is False for t in tasks):
                # 两个请求都已进入 send（阻塞在 gate）即说明路由完成
                if len(ch.send_log) >= 2:
                    break
            await asyncio.sleep(0.01)
        assert len(factory.handlers) == 1, f"single-pod must not scale, got {len(factory.handlers)}"
        ch.gate.set()
        await asyncio.gather(*tasks)
    finally:
        await sm.stop()
