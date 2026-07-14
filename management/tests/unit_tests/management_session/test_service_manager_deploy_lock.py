# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""ServiceManager：deploy 不占用全局锁，避免冷启动阻塞已绑定 session 的亲和路由。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional, cast

import pytest

from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
from openjiuwen_runtime.management.session.interfaces import (
    IResponseParser,
    IRequest,
    IServiceHandler,
    IServiceInstanceFactory,
)
from openjiuwen_runtime.management.session.runtime import NoOpDeployController
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.service_manager import ServiceManager, QueueItem
from openjiuwen_runtime.management.session.session_request import SessionRequest
from openjiuwen_runtime.management.session.timer import Timer


@dataclass
class _P(IResponseParser):
    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        return data.get("request_id")

    def is_completed(self, data: dict[str, Any]) -> bool:
        return bool(data.get("done") or data.get("completed"))

    def response(self, data: dict[str, Any]) -> Any:
        return data.get("t", data)


class _Ch:
    async def send(self, *args: Any, **kwargs: Any) -> None:
        return None


def _sreq(session_id: str, request_id: str = "r") -> SessionRequest:
    return SessionRequest(
        session_id=session_id,
        concurrency=1,
        ttl=0,
        request_id=request_id,
        raw=cast(IRequest, object()),
    )


@pytest.mark.asyncio
async def test_slow_deploy_does_not_block_affinity_pick() -> None:
    """慢 deploy 进行中时，已绑定 session 的亲和选路不应被全局锁堵住。"""
    deploy_started = asyncio.Event()
    deploy_gate = asyncio.Event()

    class SlowDeploy:
        resource_id = None

        async def deploy(self) -> None:
            deploy_started.set()
            await deploy_gate.wait()

        async def delete(self) -> None:
            return None

    class Factory(IServiceInstanceFactory):
        async def new_service(
            self,
            response_parser: IResponseParser,
            service_template: Optional[Dict[str, Any]] = None,
        ) -> IServiceHandler:
            return ServiceHandler(
                total_concurrency=1,
                message_channel=_Ch(),  # type: ignore[arg-type]
                response_parser=response_parser,
                deploy_controller=SlowDeploy(),  # type: ignore[arg-type]
                service_template=service_template,
            )

    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(8, 8)
    sm = ServiceManager(
        Factory(),
        dq,
        Timer(),
        service_concurrency=1,
        min_idle_services=0,
        max_services=3,
        service_idle_ttl=0,
        pod_monitor_enabled=False,
    )
    await sm.init(_P())
    sm._running = True  # noqa: SLF001
    sm._in_use[None] = {}  # noqa: SLF001
    sm._idle[None] = {}  # noqa: SLF001

    warm = ServiceHandler(
        total_concurrency=1,
        message_channel=_Ch(),  # type: ignore[arg-type]
        response_parser=_P(),
        deploy_controller=NoOpDeployController(),
        service_template=None,
    )
    assert warm.try_reserve_session_quota("s1", 1)
    sm._in_use[None][warm.id] = warm  # noqa: SLF001
    await sm._service_router.set_session_service("s1", warm.id)  # noqa: SLF001

    cold_task = asyncio.create_task(sm._pick_or_create(_sreq("s2", "r2")))  # noqa: SLF001
    await asyncio.wait_for(deploy_started.wait(), timeout=2.0)

    t0 = asyncio.get_running_loop().time()
    affinity_h = await asyncio.wait_for(
        sm._pick_or_create(_sreq("s1", "r1")),  # noqa: SLF001
        timeout=1.0,
    )
    elapsed = asyncio.get_running_loop().time() - t0

    assert affinity_h is warm
    assert elapsed < 0.5, f"亲和选路被 deploy 阻塞了 {elapsed:.3f}s"

    deploy_gate.set()
    cold_h = await asyncio.wait_for(cold_task, timeout=2.0)
    assert cold_h is not None
    assert cold_h is not warm

    await sm.stop()


@pytest.mark.asyncio
async def test_parallel_cold_starts_respect_max_via_pending() -> None:
    """并行冷启动用 pending 占位，不超过 max_services。"""
    gate = asyncio.Event()
    started = 0

    class SlowDeploy:
        resource_id = None

        async def deploy(self) -> None:
            nonlocal started
            started += 1
            await gate.wait()

        async def delete(self) -> None:
            return None

    class Factory(IServiceInstanceFactory):
        async def new_service(
            self,
            response_parser: IResponseParser,
            service_template: Optional[Dict[str, Any]] = None,
        ) -> IServiceHandler:
            return ServiceHandler(
                total_concurrency=1,
                message_channel=_Ch(),  # type: ignore[arg-type]
                response_parser=response_parser,
                deploy_controller=SlowDeploy(),  # type: ignore[arg-type]
                service_template=service_template,
            )

    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(16, 16)
    sm = ServiceManager(
        Factory(),
        dq,
        Timer(),
        service_concurrency=1,
        min_idle_services=0,
        max_services=2,
        service_idle_ttl=0,
        pod_monitor_enabled=False,
    )
    await sm.init(_P())
    sm._running = True  # noqa: SLF001
    sm._in_use[None] = {}  # noqa: SLF001
    sm._idle[None] = {}  # noqa: SLF001

    t1 = asyncio.create_task(sm._pick_or_create(_sreq("a", "1")))  # noqa: SLF001
    t2 = asyncio.create_task(sm._pick_or_create(_sreq("b", "2")))  # noqa: SLF001
    t3 = asyncio.create_task(sm._pick_or_create(_sreq("c", "3")))  # noqa: SLF001

    # 等待两个 pending deploy 启动
    for _ in range(50):
        if started >= 2:
            break
        await asyncio.sleep(0.02)
    assert started == 2
    assert sm._pending_deploys.get(None, 0) == 2  # noqa: SLF001

    gate.set()
    results = await asyncio.gather(t1, t2, t3)
    ok = [h for h in results if h is not None]
    assert len(ok) == 2
    assert sum(1 for h in results if h is None) == 1

    await sm.stop()


@pytest.mark.asyncio
async def test_same_session_cold_start_coalesces_deploy() -> None:
    """同 session 并发冷启动只占 1 个 pending / 只 deploy 一次，后来者等待后绑定。"""
    gate = asyncio.Event()
    started = 0

    class SlowDeploy:
        resource_id = None

        async def deploy(self) -> None:
            nonlocal started
            started += 1
            await gate.wait()

        async def delete(self) -> None:
            return None

    class Factory(IServiceInstanceFactory):
        async def new_service(
            self,
            response_parser: IResponseParser,
            service_template: Optional[Dict[str, Any]] = None,
        ) -> IServiceHandler:
            return ServiceHandler(
                total_concurrency=10,
                message_channel=_Ch(),  # type: ignore[arg-type]
                response_parser=response_parser,
                deploy_controller=SlowDeploy(),  # type: ignore[arg-type]
                service_template=service_template,
            )

    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(16, 16)
    sm = ServiceManager(
        Factory(),
        dq,
        Timer(),
        service_concurrency=10,
        min_idle_services=0,
        max_services=1,
        service_idle_ttl=0,
        pod_monitor_enabled=False,
    )
    await sm.init(_P())
    sm._running = True  # noqa: SLF001
    sm._in_use[None] = {}  # noqa: SLF001
    sm._idle[None] = {}  # noqa: SLF001

    # 同 session 3 路并发；旧逻辑会抢 3 次 pending 而 max=1 导致仅 1 成功
    tasks = [
        asyncio.create_task(sm._pick_or_create(_sreq("same-session", f"r{i}")))  # noqa: SLF001
        for i in range(3)
    ]
    for _ in range(50):
        if started >= 1:
            break
        await asyncio.sleep(0.02)
    assert started == 1
    assert sm._pending_deploys.get(None, 0) == 1  # noqa: SLF001
    assert "same-session" in sm._session_deploy_waiters  # noqa: SLF001

    gate.set()
    results = await asyncio.gather(*tasks)
    assert all(h is not None for h in results)
    assert len({h.id for h in results if h is not None}) == 1
    assert started == 1

    await sm.stop()
