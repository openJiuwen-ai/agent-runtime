# coding: utf-8
"""RM 业务层测试（M5）：watch 死 Pod（J）/ 健康探测（N）/ 孤儿对账（L）/
cleanup / update_pool_config。FakeK8s 可编程状态驱动。"""

from __future__ import annotations

import pytest

from agent_runtime.util import now_ts
from tests.conftest import requires_lua

SCOPE = "scope-main"   # 与 conftest.seed_template 播种的 scope_id 一致(route 落此 scope)


async def _deploy_one(runtime) -> str:
    """route 一个会话触发真实 acquire 链路，返回 pod_id。"""
    result = await runtime.route("sess_1")
    return result["pod_id"]


# ---------------------------------------------------------------- 场景 J：死 Pod 探测


@requires_lua
async def test_watch_purges_dead_pod_and_notifies_sm(runtime):
    """Pod 判死（Failed）→ K8s delete + PURGE + notify_pod_dead（SM 清洗）。"""
    await runtime.seed_template()
    pod_id = await _deploy_one(runtime)
    runtime.k8s.dead_pods.add(pod_id)

    await runtime.rm_sweeper.watch_once()

    assert pod_id in runtime.k8s.deleted
    assert pod_id not in await runtime.rm_state.all_pod_ids()
    # SM 侧被 notify 清洗（会话 + 注册）
    assert await runtime.sm_state.registered_pods() == []
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.session("sess_1")
    ) == 0


@requires_lua
async def test_watch_keeps_healthy_pod(runtime):
    await runtime.seed_template()
    pod_id = await _deploy_one(runtime)
    await runtime.rm_sweeper.watch_once()
    assert pod_id in await runtime.rm_state.all_pod_ids()


# ---------------------------------------------------------------- 场景 N：半死 Pod


@requires_lua
async def test_health_probe_judges_half_dead_after_two_failures(runtime):
    """连续 2 次健康探测失败 → 按死 Pod 清理；第 1 次不杀（防抖动误杀）。"""
    await runtime.seed_template()
    pod_id = await _deploy_one(runtime)
    info = await runtime.rm_state.pod_info(pod_id)
    runtime.k8s.unhealthy_pods.add(info["pod_ip"])

    await runtime.rm_sweeper.watch_once()          # 第 1 次失败：计数，不清理
    assert pod_id in await runtime.rm_state.all_pod_ids()
    fails = await runtime.rm_state.redis.get(runtime.rm_state.k.pod_health_fails(pod_id))
    assert fails is not None and int(fails) == 1

    await runtime.rm_sweeper.watch_once()          # 第 2 次失败：判半死 → 清理
    assert pod_id not in await runtime.rm_state.all_pod_ids()
    assert pod_id in runtime.k8s.deleted


@requires_lua
async def test_health_probe_reset_on_success(runtime):
    """探测恢复 → 计数清零，无动作。"""
    await runtime.seed_template()
    pod_id = await _deploy_one(runtime)
    info = await runtime.rm_state.pod_info(pod_id)
    await runtime.rm_state.bump_health_fail(pod_id)
    runtime.k8s.unhealthy_pods.discard(info["pod_ip"])

    await runtime.rm_sweeper.watch_once()
    assert await runtime.rm_state.redis.exists(
        runtime.rm_state.k.pod_health_fails(pod_id)
    ) == 0
    assert pod_id in await runtime.rm_state.all_pod_ids()


# ---------------------------------------------------------------- 场景 L：孤儿对账


@requires_lua
async def test_reconcile_moves_stale_pod_to_idle(runtime):
    """RM 持有、SM 已 ZREM（idle_consider 丢失）→ 对账转 idle → 按 pod_ttl 回收。"""
    await runtime.seed_template(pod_ttl=50)
    pod_id = await _deploy_one(runtime)
    # 模拟 idle_consider 丢失：SM 已 evict + ZREM 候选，但 RM 侧从未收到 release
    await runtime.sm_state.evict("sess_1")
    assert await runtime.sm_state.sweep_idle_notify(SCOPE, pod_id) is True

    await runtime.rm_sweeper.reconcile_once()

    assert pod_id in await runtime.rm_state.idle_pods(SCOPE)
    # idle_since 前移超 pod_ttl → reclaim 回收
    await runtime.rm_state.redis.set(
        runtime.rm_state.k.pod_idle_since(pod_id), now_ts() - 51
    )
    await runtime.rm_sweeper.reclaim_once()
    assert pod_id not in await runtime.rm_state.all_pod_ids()


@requires_lua
async def test_reconcile_does_not_touch_active_pod(runtime):
    """SM 仍在 route 的活跃 Pod 不被误判 stale。"""
    await runtime.seed_template()
    pod_id = await _deploy_one(runtime)     # sess_1 活跃，候选集含该 Pod

    await runtime.rm_sweeper.reconcile_once()
    assert pod_id not in await runtime.rm_state.idle_pods(SCOPE)
    assert pod_id in await runtime.rm_state.all_pod_ids()


@requires_lua
async def test_reconcile_purges_pod_absent_in_k8s(runtime):
    """Redis↔K8s 对账：K8s 里已不存在的 Pod → PURGE + notify（Watch 兜底）。"""
    await runtime.seed_template()
    pod_id = await _deploy_one(runtime)
    await runtime.k8s.delete(pod_id, "default")       # 直接物理删除（模拟漏报）

    await runtime.rm_sweeper.reconcile_once()
    assert pod_id not in await runtime.rm_state.all_pod_ids()
    assert await runtime.sm_state.registered_pods() == []


# ---------------------------------------------------------------- cleanup


@requires_lua
async def test_cleanup_deletes_by_label_selector(runtime):
    """cleanup 按 label 批删物理 Pod，不动 Redis 编排态（清完 autoscale 重建）。"""
    await runtime.seed_template(min_idle_pods=1)
    pod_a = await _deploy_one(runtime)
    await runtime.rm_sweeper.autoscale_once()           # 再建一个热备
    pods_before = set(await runtime.rm_state.all_pod_ids())
    assert len(pods_before) == 2

    cleaned = await runtime.rm_facade.cleanup()
    assert cleaned == 2
    assert set(runtime.k8s.deleted) == pods_before
    # Redis 编排态未被动（watch/reconcile 兜底清理）
    assert set(await runtime.rm_state.all_pod_ids()) == pods_before
    await runtime.rm_sweeper.reconcile_once()           # K8s 已无 → 对账清掉
    assert await runtime.rm_state.all_pod_ids() == []
    # 清完后 autoscale 重建热备（min_idle=1）
    await runtime.rm_sweeper.autoscale_once()
    assert len(await runtime.rm_state.idle_pods(SCOPE)) == 1


class _ApiError(Exception):
    """模拟 kubernetes_asyncio.ApiException（仅 status 属性,不引真依赖）。"""

    def __init__(self, status: int) -> None:
        super().__init__(f"status={status}")
        self.status = status


class _ListBoomK8s:
    """list_pods 按 status 抛错（404/403 分支用）,其余转发内层 FakeK8s。"""

    def __init__(self, inner, status: int) -> None:
        self._inner, self.status = inner, status

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def list_pods(self, namespace, label_selector):
        raise _ApiError(self.status)


@requires_lua
async def test_cleanup_namespace_missing_returns_zero(runtime):
    """cleanup 目标 namespace 不存在（404）→ 容忍为 cleaned=0。

    对齐 cluster 级凭据下「list 不存在 ns 得空列表」的既有行为
    （M7 经 LB 冒烟发现的跨部署形态差异,真 403 场景见下一用例）。
    """
    from agent_runtime.resource_manager.facade import ResourceManagerFacade
    from agent_runtime.resource_manager.orchestrator import ResourceOrchestrator

    boom = _ListBoomK8s(runtime.k8s, status=404)
    facade = ResourceManagerFacade(
        ResourceOrchestrator(runtime.rm_state, boom))
    assert await facade.cleanup("no-such-ns", None) == 0


@requires_lua
async def test_cleanup_rbac_forbidden_fails_fast(runtime):
    """cleanup 无权限（403）→ 快速失败（静默清零会掩盖 RBAC 配错）。"""
    from agent_runtime.resource_manager.facade import ResourceManagerFacade
    from agent_runtime.resource_manager.orchestrator import ResourceOrchestrator

    boom = _ListBoomK8s(runtime.k8s, status=403)
    facade = ResourceManagerFacade(
        ResourceOrchestrator(runtime.rm_state, boom))
    with pytest.raises(_ApiError):
        await facade.cleanup("unauthorized-ns", None)


# ---------------------------------------------------------------- update_pool_config


@requires_lua
async def test_update_pool_config_overwrites_immediately(runtime):
    await runtime.seed_template()
    await _deploy_one(runtime)                          # 建 scope:config

    await runtime.rm_facade.update_pool_config(
        SCOPE, {"min_idle_pods": 2, "max_pods": 5, "pod_ttl": 120}
    )
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg["min_idle_pods"] == "2"
    assert cfg["max_pods"] == "5"
    assert cfg["pod_ttl"] == "120"
    # 立即生效：autoscale 按 min_idle=2 补位
    await runtime.rm_sweeper.autoscale_once()
    await runtime.rm_sweeper.autoscale_once()
    assert len(await runtime.rm_state.idle_pods(SCOPE)) == 2


@requires_lua
async def test_update_pool_config_with_pod_spec_refreshes_deploy_ver(runtime):
    """A 类推送附带 pod_spec → RM 缓存新 deploy_ver + 新 deploy 字段。"""
    await runtime.seed_template(agent_image="agentserver:1.0")
    await _deploy_one(runtime)
    old_ver = (await runtime.rm_state.load_scope_config(SCOPE))["deploy_ver"]

    from agent_runtime.session_manager.models import Template

    new_template = Template(template_id="tpl-1", agent_image="agentserver:9.0")
    await runtime.rm_facade.update_pool_config(
        SCOPE, {"min_idle_pods": 0, "max_pods": 2, "pod_ttl": 300},
        pod_spec=new_template.deploy_subset(),
    )
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg["deploy_ver"] != old_ver
    assert "agentserver:9.0" in cfg["pod_spec_json"]
