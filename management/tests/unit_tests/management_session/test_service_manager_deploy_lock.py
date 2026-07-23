# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""ServiceManager：deploy 不占用全局锁，避免冷启动阻塞池内容量选路。"""

# G.CLS.11（建议级）针对生产封装，不适用于此类白盒测试，故统一豁免。
# pylint: disable=protected-access

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
    MessageType,
    RawMessage,
)
from openjiuwen_runtime.management.session.internal_events import ServiceReclaimEvent
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
async def test_slow_deploy_does_not_block_capacity_pick() -> None:
    """慢 deploy 进行中时，池内仍有容量的选路不应被全局锁堵住。

    develop 上亲和在 SessionRuntimeManager；此处验证 unlock deploy 不堵容量选池。
    """
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
    sm._in_use[None][warm.id] = warm  # noqa: SLF001

    # 后台慢 deploy（已占位），模拟冷启动进行中
    async with sm._lock:  # noqa: SLF001
        sm._begin_pending_deploy_locked(None)  # noqa: SLF001
    cold_task = asyncio.create_task(
        sm._deploy_and_admit(None, None, into="in_use")  # noqa: SLF001
    )
    await asyncio.wait_for(deploy_started.wait(), timeout=2.0)

    t0 = asyncio.get_running_loop().time()
    picked = await asyncio.wait_for(
        sm._pick_or_create(_sreq("s1", "r1")),  # noqa: SLF001
        timeout=1.0,
    )
    elapsed = asyncio.get_running_loop().time() - t0

    assert picked is warm
    assert elapsed < 0.5, f"容量选路被 deploy 阻塞了 {elapsed:.3f}s"

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
    """同 session 并发冷启动只占 1 个 pending / 只 deploy 一次，后来者等待后选池。"""
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


@pytest.mark.asyncio
async def test_stop_during_deploy_does_not_double_delete() -> None:
    """锁外 deploy 完成时若 stop 已认领实例，orphan 路径不得二次 delete。"""
    deploy_started = asyncio.Event()
    deploy_gate = asyncio.Event()
    delete_calls = 0

    class SlowDeploy:
        def __init__(self) -> None:
            self._resource_id: str | None = None

        @property
        def resource_id(self) -> str | None:
            return self._resource_id

        async def deploy(self) -> None:
            # 先占用 resource_id，模拟 K8s 已创建 Pod、等待 ready 的窗口
            self._resource_id = "pod-under-stop"
            deploy_started.set()
            await deploy_gate.wait()

        async def delete(self) -> str:
            nonlocal delete_calls
            delete_calls += 1
            rid = self._resource_id or ""
            self._resource_id = None
            return rid

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

    async with sm._lock:  # noqa: SLF001
        sm._begin_pending_deploy_locked(None, into="idle")  # noqa: SLF001
    admit_task = asyncio.create_task(
        sm._deploy_and_admit(None, None, into="idle")  # noqa: SLF001
    )
    await asyncio.wait_for(deploy_started.wait(), timeout=2.0)

    stop_task = asyncio.create_task(sm.stop())
    # 等 stop 在 deploy 尚未完成时先 delete 一次，再放行 deploy 完成
    for _ in range(80):
        if delete_calls >= 1:
            break
        await asyncio.sleep(0.02)
    assert delete_calls == 1, f"stop 应在 deploy 窗口内 delete 一次，实际 {delete_calls}"

    deploy_gate.set()
    admitted = await admit_task
    await stop_task
    assert admitted is None
    assert delete_calls == 1, f"orphan 路径不应二次 delete，实际 {delete_calls}"


@pytest.mark.asyncio
async def test_concurrent_min_idle_fill_counts_pending_idle() -> None:
    """锁外预热期间 autoscale 并发补齐时，pending_idle 应计入 min_idle，只拉 1 台。"""
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

    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(8, 8)
    sm = ServiceManager(
        Factory(),
        dq,
        Timer(),
        service_concurrency=1,
        min_idle_services=1,
        max_services=10,
        service_idle_ttl=0,
        pod_monitor_enabled=False,
    )
    await sm.init(_P())
    sm._running = True  # noqa: SLF001
    sm._in_use[None] = {}  # noqa: SLF001
    sm._idle[None] = {}  # noqa: SLF001

    boot = asyncio.create_task(
        sm._fill_min_idle_for_template(  # noqa: SLF001
            None,
            min_idle_services=1,
            max_services=10,
            deploy_template=None,
            log_prefix="预拉热",
        )
    )
    for _ in range(50):
        if started >= 1:
            break
        await asyncio.sleep(0.02)
    assert started == 1
    assert sm._pending_idle_deploys.get(None, 0) == 1  # noqa: SLF001

    # 模拟 autoscale 在 deploy 未完成时再次补齐
    ensure = asyncio.create_task(
        sm._fill_min_idle_for_template(  # noqa: SLF001
            None,
            min_idle_services=1,
            max_services=10,
            deploy_template=None,
            log_prefix="autoscale",
        )
    )
    await asyncio.sleep(0.05)
    assert started == 1, "pending_idle 未计入 min_idle，autoscale 又拉起了第二台"

    gate.set()
    await asyncio.gather(boot, ensure)
    assert len(sm._idle.get(None, {})) == 1  # noqa: SLF001
    assert sm._pending_idle_deploys.get(None, 0) == 0  # noqa: SLF001

    await sm.stop()


@pytest.mark.asyncio
async def test_stop_waits_for_in_flight_reclaim_delete() -> None:
    """stop 不得 cancel 已 pop 的缩容：delete 必须完成，否则会留下幽灵实例。"""
    delete_started = asyncio.Event()
    delete_gate = asyncio.Event()
    delete_calls = 0

    class SlowDeleteDeploy:
        resource_id = "pod-idle-1"

        async def deploy(self) -> None:
            return None

        async def delete(self) -> None:
            nonlocal delete_calls
            delete_calls += 1
            delete_started.set()
            await delete_gate.wait()

    class Factory(IServiceInstanceFactory):
        async def new_service(
            self,
            response_parser: IResponseParser,
            service_template: Optional[Dict[str, Any]] = None,
        ) -> IServiceHandler:
            return ServiceHandler(
                service_id="idle-to-drop",
                total_concurrency=1,
                message_channel=_Ch(),  # type: ignore[arg-type]
                response_parser=response_parser,
                deploy_controller=SlowDeleteDeploy(),  # type: ignore[arg-type]
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
    h = await Factory().new_service(_P())
    sm._idle.setdefault(None, {})[h.id] = h  # noqa: SLF001
    sm._running = True  # noqa: SLF001

    reclaim_task = asyncio.create_task(sm._on_service_reclaim(h.id))  # noqa: SLF001
    sm._reclaim_tasks.add(reclaim_task)  # noqa: SLF001
    reclaim_task.add_done_callback(sm._discard_reclaim_task)  # noqa: SLF001

    await asyncio.wait_for(delete_started.wait(), timeout=2.0)
    assert h.id not in sm._idle.get(None, {})  # noqa: SLF001
    assert delete_calls == 1

    stop_task = asyncio.create_task(sm.stop())
    await asyncio.sleep(0.05)
    assert not stop_task.done(), "stop 应等待在途缩容 delete"
    assert delete_calls == 1

    delete_gate.set()
    await asyncio.wait_for(stop_task, timeout=2.0)
    assert delete_calls == 1


@pytest.mark.asyncio
async def test_reclaim_occupancy_counts_toward_max_until_delete_done() -> None:
    """缩容 pop 后、delete 完成前仍占用 max 名额，避免第二轮抢跑扩容。"""
    delete_started = asyncio.Event()
    delete_gate = asyncio.Event()

    class SlowDeleteDeploy:
        resource_id = "pod-x"

        async def deploy(self) -> None:
            return None

        async def delete(self) -> None:
            delete_started.set()
            await delete_gate.wait()

    class Factory(IServiceInstanceFactory):
        async def new_service(
            self,
            response_parser: IResponseParser,
            service_template: Optional[Dict[str, Any]] = None,
        ) -> IServiceHandler:
            return ServiceHandler(
                service_id="idle-to-drop",
                total_concurrency=1,
                message_channel=_Ch(),  # type: ignore[arg-type]
                response_parser=response_parser,
                deploy_controller=SlowDeleteDeploy(),  # type: ignore[arg-type]
                service_template=service_template,
            )

    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(8, 8)
    sm = ServiceManager(
        Factory(),
        dq,
        Timer(),
        service_concurrency=1,
        min_idle_services=0,
        max_services=1,
        service_idle_ttl=0,
        pod_monitor_enabled=False,
    )
    await sm.init(_P())
    sm._running = True  # noqa: SLF001
    h = await Factory().new_service(_P())
    sm._idle.setdefault(None, {})[h.id] = h  # noqa: SLF001

    reclaim_task = asyncio.create_task(sm._on_service_reclaim(h.id))  # noqa: SLF001
    await asyncio.wait_for(delete_started.wait(), timeout=2.0)
    assert h.id not in sm._idle.get(None, {})  # noqa: SLF001
    assert sm._total_services_by_template(None) == 1  # noqa: SLF001
    assert sm._try_begin_deploy_locked(  # noqa: SLF001
        _sreq("other"), need=1, template_id=None
    ) is None

    delete_gate.set()
    await reclaim_task
    assert sm._reclaim_occupancy.get(None, 0) == 0  # noqa: SLF001
    await sm.stop()


@pytest.mark.asyncio
async def test_slow_reclaim_does_not_block_message_loop_user_route() -> None:
    """缩容 delete 缓慢时，message_loop 仍应继续 dequeue 并 spawn 用户路由。"""
    reclaim_started = asyncio.Event()
    reclaim_gate = asyncio.Event()
    user_started = asyncio.Event()

    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(8, 8)
    sm = ServiceManager(
        cast(IServiceInstanceFactory, object()),
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

    async def slow_reclaim(_service_id: str) -> None:
        reclaim_started.set()
        await reclaim_gate.wait()

    class _FakeRT:
        async def handle_user_request(self, _raw: RawMessage) -> None:
            user_started.set()

        async def shutdown(self) -> None:
            return None

    sm._on_service_reclaim = slow_reclaim  # noqa: SLF001
    sm._session_runtime = _FakeRT()  # noqa: SLF001

    await sm.enqueue_system(ServiceReclaimEvent(service_id="idle-to-drop"))
    await sm._q.put_user(  # noqa: SLF001
        RawMessage(MessageType.USER_REQUEST, object())
    )

    loop_task = asyncio.create_task(sm._message_loop())  # noqa: SLF001
    try:
        await asyncio.wait_for(reclaim_started.wait(), timeout=2.0)
        # 旧逻辑在 await delete/reclaim 期间无法取用户队列，这里必须很快观察到用户 task
        await asyncio.wait_for(user_started.wait(), timeout=0.5)
        assert not reclaim_gate.is_set()
        assert len(sm._reclaim_tasks) >= 1  # noqa: SLF001
    finally:
        reclaim_gate.set()
        sm._running = False  # noqa: SLF001
        sm._q.mark_closed()  # noqa: SLF001
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await sm.stop()


@pytest.mark.asyncio
async def test_deploy_and_admit_displaces_same_service_id_and_deletes_old() -> None:
    """同名 service_id 再次入池时，须驱逐并 delete 旧 handler，避免 K8s 孤儿 Pod。"""
    deleted: list[str] = []

    class TrackingDeploy:
        def __init__(self, name: str) -> None:
            self.resource_id = name
            self.name = name

        async def deploy(self) -> None:
            return None

        async def delete(self) -> None:
            deleted.append(self.name)

    class Factory(IServiceInstanceFactory):
        def __init__(self) -> None:
            self.n = 0

        async def new_service(
            self,
            response_parser: IResponseParser,
            service_template: Optional[Dict[str, Any]] = None,
        ) -> IServiceHandler:
            self.n += 1
            rid = f"pod-{self.n}"
            return ServiceHandler(
                service_id="loadtest_s16bot_main",
                total_concurrency=10,
                message_channel=_Ch(),  # type: ignore[arg-type]
                response_parser=response_parser,
                deploy_controller=TrackingDeploy(rid),  # type: ignore[arg-type]
                service_template=service_template,
            )

    factory = Factory()
    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(8, 8)
    sm = ServiceManager(
        factory,
        dq,
        Timer(),
        service_concurrency=10,
        min_idle_services=0,
        max_services=5,
        service_idle_ttl=0,
        pod_monitor_enabled=False,
    )
    await sm.init(_P())
    sm._running = True  # noqa: SLF001
    sm._in_use[None] = {}  # noqa: SLF001
    sm._idle[None] = {}  # noqa: SLF001

    removed_pods: list[str] = []

    class _FakeRT:
        async def on_pod_removed(self, service_id: str) -> None:
            removed_pods.append(service_id)

        async def shutdown(self) -> None:
            return None

    sm._session_runtime = _FakeRT()  # noqa: SLF001

    old = await factory.new_service(_P())
    assert old.try_reserve_session_quota("sticky-other-group", 10)
    sm._in_use[None][old.id] = old  # noqa: SLF001

    sm._begin_pending_deploy_locked(None, into="in_use")  # noqa: SLF001
    new_h = await sm._deploy_and_admit(None, None, into="in_use")  # noqa: SLF001

    assert new_h is not None
    assert new_h is not old
    assert new_h.id == "loadtest_s16bot_main"
    assert sm._in_use[None][new_h.id] is new_h  # noqa: SLF001
    assert deleted == ["pod-1"]
    assert removed_pods == [old.id]

    await sm.stop()
