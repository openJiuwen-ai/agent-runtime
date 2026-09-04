# coding: utf-8
"""RM 韧性测试:异常生命周期路径(缺陷⑤回归网)。

2026-08-26 真环境实测教训:优雅停机取消在飞的 autoscale deploy tick 时,
``except Exception`` 接不住 ``CancelledError``(BaseException),deploying 占位
不清 → 泄漏占位计入 max_pods 把池永久堵死(route 恒 NO_POD_AVAILABLE)。
红线「错误路径必须清占位」必须覆盖取消路径。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from agent_runtime.errors import DeployFailed
from agent_runtime.resource_manager import orchestrator as rm_orch_module
from agent_runtime.resource_manager.orchestrator import ResourceOrchestrator
from agent_runtime.resource_manager.sweeper import ResourceSweeper
from agent_runtime.util import now_ts
from tests.conftest import requires_lua

SCOPE = "scope-resilience"


class _SpySM:
    """notify_pod_dead 侦听（断言场景 G 通知链）。"""

    def __init__(self) -> None:
        self.dead_notifications: list[str] = []

    async def notify_pod_dead(self, pod_id: str) -> None:
        self.dead_notifications.append(pod_id)


class _SlowK8s:
    """包装 FakeK8s:deploy 人为延迟(制造取消窗口),其余转发。"""

    def __init__(self, inner, deploy_delay: float) -> None:
        self._inner = inner
        self.deploy_delay = deploy_delay
        self.in_deploy = asyncio.Event()
        self.deploys = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def deploy(self, pod_spec):
        self.deploys += 1
        self.in_deploy.set()
        await asyncio.sleep(self.deploy_delay)     # 取消窗口
        return await self._inner.deploy(pod_spec)


@pytest.fixture
def nat_cfg():
    """合法 pod_spec(autoscale 预热依赖 pod_spec_json 非空)。"""
    return {
        "agent_image": "agentserver:1.0", "namespace": "default",
        "sse_port": 8080, "sse_path": "/sse",
    }


@requires_lua
async def test_shutdown_cancel_mid_deploy_clears_placeholder(rm_state, k8s, nat_cfg):
    """部署中途取消(优雅停机语义)→ deploying 占位必须清(红线),不泄漏。"""
    slow = _SlowK8s(k8s, deploy_delay=0.5)
    sweeper = ResourceSweeper(
        rm_state, slow, orchestrator=ResourceOrchestrator(rm_state, slow),
        sm_facade=None,
    )
    await rm_state.save_scope_config(SCOPE, {
        "min_idle_pods": 1, "max_pods": 2, "pod_ttl": 300,
        "deploy_ver": "ver1", "pod_spec_json": json.dumps(nat_cfg),
    })

    task = asyncio.create_task(sweeper.autoscale_once())
    await asyncio.wait_for(slow.in_deploy.wait(), timeout=2)   # 进入 deploy 内
    task.cancel()                                              # 模拟停机取消
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await rm_state.deploying_count(SCOPE) == 0, "取消路径泄漏 deploying 占位"
    assert await rm_state.idle_pods(SCOPE) == []                # 未注册进池
    # 锁与占位都不留 → 后续 autoscale 可正常补位(池未被堵死)
    assert await rm_state.redis.exists(rm_state.k.lock_deploy(SCOPE)) == 0


@requires_lua
async def test_redeploy_after_cancel_succeeds(rm_state, k8s, nat_cfg):
    """取消后下一拍 autoscale 正常完成(池自愈,占位不残留导致的 max 堵死不存在)。"""
    slow = _SlowK8s(k8s, deploy_delay=0.5)
    orchestrator = ResourceOrchestrator(rm_state, slow)
    sweeper = ResourceSweeper(rm_state, slow, orchestrator=orchestrator,
                              sm_facade=None)
    await rm_state.save_scope_config(SCOPE, {
        "min_idle_pods": 1, "max_pods": 2, "pod_ttl": 300,
        "deploy_ver": "ver1", "pod_spec_json": json.dumps(nat_cfg),
    })

    first = asyncio.create_task(sweeper.autoscale_once())
    await asyncio.wait_for(slow.in_deploy.wait(), timeout=2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    slow.deploy_delay = 0                       # 第二拍立即完成
    await sweeper.autoscale_once()
    assert len(await rm_state.idle_pods(SCOPE)) == 1            # 热备入池
    assert await rm_state.deploying_count(SCOPE) == 0
    assert await rm_state.pod_count(SCOPE) == 1


@requires_lua
async def test_register_failure_deletes_orphan_pod(rm_state, k8s, nat_cfg, monkeypatch):
    """REGISTER 步失败（Redis 异常不带 pod_id 属性）：物理 Pod 已建且 Ready，
    必须用已到手的 info 兜底删除——否则成 pods:all 之外的孤儿（watch/reconcile
    只做 Redis→K8s 单向对账，无人认领、无上界累积）。"""
    orchestrator = ResourceOrchestrator(rm_state, k8s)

    async def _register_boom(**kwargs):
        raise RuntimeError("simulated redis outage during REGISTER")

    monkeypatch.setattr(rm_state, "register_pod", _register_boom)
    with pytest.raises(DeployFailed):
        await orchestrator._deploy_and_register(
            SCOPE, nat_cfg, "ver1", "tok-1", idle_flag=True)

    assert len(k8s.deleted) == 1, "register 失败后未用 info 兜底删除物理 Pod"
    assert not k8s.pods, "假集群里不应残留孤儿 Pod"


@requires_lua
async def test_purge_skipped_when_k8s_delete_fails_then_retries(rm_state, k8s):
    """delete 非 404 失败：本拍不得 PURGE（记录一清，存活 Pod 就脱离 Redis
    枚举源成孤儿）；恢复后下拍重试才清、通知恰一次。"""
    sweeper = ResourceSweeper(rm_state, k8s, sm_facade=_SpySM())
    await rm_state.register_pod(
        pod_id="pod-purge-1", scope_id=SCOPE,
        pod_sse_url="http://10.42.0.9:8080/sse", pod_ip="10.42.0.9",
        namespace="default", deploy_ver="ver1", deploy_token="tok-purge",
        idle_flag=True, now=now_ts(), sse_port=8080, health_path="/health",
    )
    spy = sweeper.sm

    k8s.delete_failures = 1
    await sweeper._purge_and_notify("pod-purge-1")
    assert await rm_state.pod_ids(SCOPE) == ["pod-purge-1"]    # RM 记录未清
    assert await rm_state.pod_info("pod-purge-1")               # info 仍在
    assert spy.dead_notifications == []                         # 未误通知
    assert k8s.deleted == []                                    # 物理删除未成功

    await sweeper._purge_and_notify("pod-purge-1")              # 下拍重试
    assert await rm_state.pod_ids(SCOPE) == []
    assert not await rm_state.pod_info("pod-purge-1")
    assert k8s.deleted == ["pod-purge-1"]
    assert spy.dead_notifications == ["pod-purge-1"]


@requires_lua
async def test_acquire_no_config_is_bounded(rm_state, k8s, monkeypatch):
    """config 键被并发删/残缺时 no_config 重跑必须有界+退避——不得热自旋挂死
    （排障表现为「acquire 卡死」）。"""
    monkeypatch.setattr(rm_orch_module, "NO_CONFIG_MAX_LOOPS", 3)
    monkeypatch.setattr(rm_orch_module, "NO_CONFIG_BACKOFF_SEC", 0.01)
    orchestrator = ResourceOrchestrator(rm_state, k8s)

    async def _always_configured(scope_id):  # 欺骗首见检查（不落 config）
        return True

    monkeypatch.setattr(rm_state, "has_scope_config", _always_configured)
    t0 = time.monotonic()
    with pytest.raises(DeployFailed, match="no pool config"):
        await orchestrator.acquire(
            SCOPE, {"agent_image": "agentserver:1.0"},
            {"max_pods": 2}, request_id="req-nc",
        )
    assert time.monotonic() - t0 < 2, "no_config 重跑应有界退出"


@requires_lua
async def test_watch_isolates_per_pod_failure(rm_state, k8s, monkeypatch):
    """逐 Pod 隔离：all_pod_ids 是 sorted 确定序，首个 Pod 的 get_pod 异常
    不得中止整拍——否则次个 Pod 永远得不到判死清理（半死 Pod 盲区）。"""
    sweeper = ResourceSweeper(rm_state, k8s, sm_facade=_SpySM())
    for pod_id, ip in (("pod-a", "10.42.0.1"), ("pod-b", "10.42.0.2")):
        await rm_state.register_pod(
            pod_id=pod_id, scope_id=SCOPE,
            pod_sse_url=f"http://{ip}:8080/sse", pod_ip=ip,
            namespace="default", deploy_ver="ver1", deploy_token=f"tok-{pod_id}",
            idle_flag=True, now=now_ts(), sse_port=8080, health_path="/health",
        )
    k8s.dead_pods.add("pod-b")
    original = k8s.get_pod

    async def _get_pod(pod_id, namespace):
        if pod_id == "pod-a":
            raise RuntimeError("simulated apiserver hiccup")
        return await original(pod_id, namespace)

    monkeypatch.setattr(k8s, "get_pod", _get_pod)
    await sweeper.watch_once()

    assert await rm_state.pod_ids(SCOPE) == ["pod-a"]        # b 清理、a 保留待下拍
    assert sweeper.sm.dead_notifications == ["pod-b"]


@requires_lua
async def test_autoscale_isolates_per_scope_failure(rm_state, k8s, nat_cfg, monkeypatch):
    """per-scope 隔离：一个 scope 的瞬时异常不得中止其余 scope 的热备补位。"""
    orchestrator = ResourceOrchestrator(rm_state, k8s)
    sweeper = ResourceSweeper(rm_state, k8s, orchestrator=orchestrator,
                              sm_facade=None)
    for sid in ("scope-a", "scope-b"):
        await rm_state.save_scope_config(sid, {
            "min_idle_pods": 1, "max_pods": 2, "pod_ttl": 300,
            "deploy_ver": "ver1", "pod_spec_json": json.dumps(nat_cfg),
        })
    original = rm_state.load_scope_config

    async def _load(scope_id):
        if scope_id == "scope-a":
            raise RuntimeError("simulated redis error")
        return await original(scope_id)

    monkeypatch.setattr(rm_state, "load_scope_config", _load)
    await sweeper.autoscale_once()

    assert len(await rm_state.idle_pods("scope-b")) == 1     # 健康 scope 照常补位
    assert await rm_state.idle_pods("scope-a") == []
