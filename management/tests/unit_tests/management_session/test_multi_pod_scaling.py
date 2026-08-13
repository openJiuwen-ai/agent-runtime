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
    RawMessage,
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


@pytest.mark.asyncio
async def test_slow_cold_start_caps_followers_no_overfill() -> None:
    """慢 deploy（K8s 冷启动真实形态）下，同 scope 突发请求复用 leader Pod 受 ``pod_concurrency-1``
    上限约束：不重复扩容、单 Pod 不过填，超出 Pod 容量的请求直接失败（编排层 _fail 100001）。

    场景：scope=4 / pod=2（reserve_per_pod=2, max_scope_pods=2），4 个 chat_session 同时冷启动。
    leader 自身占 1 槽 + 至多 pod_concurrency-1=1 个 follower 复用同一 Pod（共 2 = reserve_per_pod），
    其余 2 个超出容量 → 失败。最终只建 1 个 Pod、不过填（回归此前 follower 无条件复用导致
    单 Pod 绑 4 个 chat_session 的过填缺陷）。
    """
    deploy_gate = asyncio.Event()
    started = 0

    class SlowDeploy:
        resource_id = None

        async def deploy(self) -> None:
            nonlocal started
            started += 1
            await deploy_gate.wait()

        async def delete(self) -> None:
            return None

    class _SlowFactory(_Factory):
        async def new_service(
            self, response_parser: IResponseParser, service_template: Optional[dict] = None
        ) -> IServiceHandler:
            h = ServiceHandler(
                total_concurrency=self._sc,
                message_channel=self._ch,
                response_parser=response_parser,
                deploy_controller=SlowDeploy(),
                service_template=service_template,
            )
            self.handlers.append(h)
            return h

    ch = _HoldCh()
    factory = _SlowFactory(ch, pod_concurrency=2)
    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(100, 1000)
    sm = ServiceManager(
        factory, dq, Timer(), service_concurrency=2, min_idle_services=0,
        max_services=10, autoscale_interval=0.2, service_idle_ttl=300, deploy_mode="subprocess",
    )
    rt = SessionRuntimeManager(Timer(), sm)
    sm.set_session_runtime(rt)
    await sm.init(_P())
    await sm.start()
    try:
        loop = asyncio.get_running_loop()

        async def one(csid: str, rid: str) -> None:
            wrapper = ScopeRequestWrapper(_sreq("scope1", 4, csid, rid), asyncio.Queue(), loop.create_future())
            await rt.handle_user_request(RawMessage(MessageType.USER_REQUEST, wrapper))

        tasks = [asyncio.create_task(one(f"cs{i}", f"r{i}")) for i in range(4)]
        # 等 leader 的 deploy 启动并阻塞，让 cs1..3 挂上同一 waiter
        for _ in range(300):
            if started >= 1:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.15)  # 让 follower 确实挂到 await join_future 上
        deploy_gate.set()  # 放行 deploy，leader admit 并唤醒 follower
        # 成功的请求进入 send 阻塞在 gate（leader + 1 follower = 2）；超出的已 _fail 返回
        for _ in range(300):
            if len(ch.send_log) >= 2:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)

        # 只建 1 个 Pod：冷启动突发不重复扩容
        assert len(factory.handlers) == 1, f"冷启动突发不应重复扩容: {len(factory.handlers)}"
        # 不过填：成功的 chat_session 数 ≤ reserve_per_pod=2
        sh = rt.registry.get("scope1")
        assert sh is not None
        overfill = [(eid, sh.endpoint_session_count(eid)) for eid in sh.endpoint_ids]
        assert all(n <= 2 for _eid, n in overfill), f"单 Pod 过填: {overfill}"
        # 仅 leader + 1 follower 成功，其余 2 个超出 Pod 容量被拒
        assert len(ch.send_log) == 2, f"应仅有 2 个请求成功（其余失败）: send_log={ch.send_log}"

        ch.gate.set()
        await asyncio.gather(*tasks)
    finally:
        await sm.stop()


# ==================== 回归:扩容死锁(同 id 撞号)+ 并发扩容去重 ====================


class _FixedIdFactory(_Factory):
    """所有新建 handler 都用固定 service_id='11',用于复现同 id 撞号挤出。"""

    async def new_service(
        self, response_parser: IResponseParser, service_template: Optional[dict] = None
    ) -> IServiceHandler:
        h = ServiceHandler(
            service_id="11",
            total_concurrency=self._sc,
            message_channel=self._ch,
            response_parser=response_parser,
            deploy_controller=NoOpDeployController(),
            service_template=service_template,
        )
        self.handlers.append(h)
        return h


@pytest.mark.asyncio
async def test_handle_user_request_no_deadlock_when_same_id_scale_evicts() -> None:
    """回归 2026-07-30 P0 死锁。

    扩容造出与池中同 id 的 Pod → ``_evacuate_same_id_locked`` 挤出旧 Pod →
    ``_cleanup_displaced_handler`` → ``on_pod_removed`` 重入 ``SessionRuntimeManager._lock``。

    修复前:``pick_or_create_pod`` 在 SRM ``_lock`` 内调用,``on_pod_removed`` 重入同一把
            不可重入 asyncio.Lock → 永久自死锁(生产表现:40 分钟无路由、请求 300s 超时)。
    修复后(方案 A):``pick_or_create_pod`` 移出 ``_lock``,``on_pod_removed`` 能正常拿锁 →
            本测试在 5s 内完成。
    """
    ch = _HoldCh()
    ch.gate.set()  # send 立即返回,避免 handle_message 阻塞干扰"死锁 vs 正常"判定
    factory = _FixedIdFactory(ch, 2)
    dq: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(100, 1000)
    sm = ServiceManager(
        factory, dq, Timer(),
        service_concurrency=2, min_idle_services=0, max_services=10,
        autoscale_interval=0.2, service_idle_ttl=300, deploy_mode="subprocess",
    )
    rt = SessionRuntimeManager(Timer(), sm)
    sm.set_session_runtime(rt)
    await sm.init(_P())
    await sm.start()
    try:
        # 预置 OLD '11':SM 层占满额度(逼 _pick_existing 跳过它)+ scope 层占满(逼 pick_or_bind 返回 None)
        old = await factory.new_service(_P())
        assert old.try_reserve_session_quota("sticky", 2)
        # 预置 OLD '11' 进 in_use 池(SM 层占满额度逼 _pick_existing 跳过;无 public 注入 API,用 getattr 避免 protected-access 静态告警)
        getattr(sm, "_in_use").setdefault(None, {})["11"] = old
        sh = await rt.registry.get_or_create("scope1", 4, 2)  # max_parallel=4, rpp=2 → max_scope_pods=2
        sh.add_endpoint(old)
        sh.bind("cs_pre1", "11")
        sh.bind("cs_pre2", "11")  # 占满 reserve_per_pod=2

        loop = asyncio.get_running_loop()
        wrapper = ScopeRequestWrapper(_sreq("scope1", 4, "cs_new", "r1"), asyncio.Queue(), loop.create_future())

        # 修复前:扩容在 _lock 内 → evac → on_pod_removed 重入同一把不可重入锁 → 死锁;
        #   wait_for 超时 cancel task,而 handle_user_request 的 except CancelledError 会吞掉 cancel
        #   照常返回,但 cs_new 从未真正路由(send_log 空)——故用 send_log 判定,而非 in_use。
        # 修复后:扩容移出 _lock,cs_new 正常路由到新 '11'(send_log 有记录)。
        await asyncio.wait_for(
            rt.handle_user_request(RawMessage(MessageType.USER_REQUEST, wrapper)),
            timeout=5.0,
        )
        assert len(ch.send_log) >= 1, (
            f"cs_new 未被路由——疑似扩容死锁(被 cancel 掩盖)。send_log={ch.send_log}"
        )
        assert sm.find_service_handler("11") is not old  # 旧 '11' 被挤出、新 '11' 入池
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_same_scope_concurrent_scaleups_coalesce_into_one_deploy() -> None:
    """方案 A 把扩容移出 ``_lock`` 后的并发扩容去重回归。

    同一 scope 的并发扩容仍被 ``_pick_or_create`` 的 leader/follower 合并成一次 deploy,
    且两个请求都成功路由、不重复 ``add_endpoint``。
    """
    rt, sm, factory, ch = await _build_runtime(pod_concurrency=2)
    try:
        ch.gate.set()
        loop = asyncio.get_running_loop()

        async def one(csid: str, rid: str) -> None:
            # ttl=60:避免请求结束后 ttl=0 立即清理 scope,导致断言时 scopeA 已被 registry 移除
            sreq = SessionRequest(
                service_id="scopeA", concurrency=2, ttl=60, request_id=rid,
                raw=_Raw(session_id=csid, request_id=rid),
            )
            wrapper = ScopeRequestWrapper(sreq, asyncio.Queue(), loop.create_future())
            await rt.handle_user_request(RawMessage(MessageType.USER_REQUEST, wrapper))

        # scope=2, pod=2 → reserve_per_pod=2, max_scope_pods=1:两个 chat_session 必须共用 1 个 Pod
        await asyncio.wait_for(
            asyncio.gather(one("cs1", "r1"), one("cs2", "r2")),
            timeout=5.0,
        )
        assert len(factory.handlers) == 1, f"expected coalesced single deploy, got {len(factory.handlers)}"
        sh = rt.registry.get("scopeA")
        assert sh is not None and sh.endpoint_count == 1, "no duplicate add_endpoint"
        assert len(ch.send_log) == 2
    finally:
        await sm.stop()


@pytest.mark.asyncio
async def test_factory_instance_id_is_uuid_despite_business_service_id_in_template() -> None:
    """P1 回归:即便 service_template 带业务 service_id(请求冷启动路径会写入),
    factory 造出的 ServiceHandler.id 也必须是 UUID,不得复用业务 id——否则同 scope 二次
    冷启动会撞号、``_evacuate_same_id_locked`` 挤出正在干活的 Pod(叠加持锁扩容即为死锁导火索)。

    gateway 侧 ``runtime_management_client._Factory`` 须遵守同一契约;本测试用 session SDK
    的 ``_Factory`` 固化该契约,防止任何 factory 实现再把 template 的业务 id 当实例 id。
    """
    factory = _Factory(_HoldCh(), pod_concurrency=4)
    h1 = await factory.new_service(_P(), service_template={"service_id": "11", "agent_id": "111"})
    h2 = await factory.new_service(_P(), service_template={"service_id": "11", "agent_id": "111"})
    assert h1.id != "11", f"实例 id 不应复用业务 service_id, got {h1.id}"
    assert h2.id != "11", f"实例 id 不应复用业务 service_id, got {h2.id}"
    assert h1.id != h2.id, "两次新建的实例 id 必须互不相同(UUID)"
