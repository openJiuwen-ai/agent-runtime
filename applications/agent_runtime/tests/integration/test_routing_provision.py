# coding: utf-8
"""routing 供给模式集成测试（2026-10，AGENT_RUNTIME_DEPLOY_MODE=routing）：

外部预建 Pod（host 域名形态）注册 → 分配 → 探测判死 → 驱逐 → 收养重建
→ 再分配全链；外加五个版本豁免点、哨兵判满、池空快失败、cleanup 拒绝、
scope 删除 drop_declared、config_refresh 不被外部 Pod 卡 409。全程
FakeK8s 零 deploy/delete/get 断言（零 K8s 权限环境的等价约束）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.errors import InvalidParams, NoPodAvailable
from agent_runtime.session_manager.orchestrator import (
    ROUTING_MAX_PODS_SENTINEL,
)
from agent_runtime.util import now_ts
from tests.conftest import Runtime, requires_lua

SCOPE = "scope-main"


def _entry(name: str, host: str | None = None) -> dict:
    return {"name": name, "host": host or f"{name}.as-svc.agentserver.svc"}


async def _register(rt, *entries: dict) -> dict:
    return await rt.rm_facade.register_pods(SCOPE, list(entries))


async def _seed(rt, **overrides) -> None:
    await rt.seed_template(**overrides)


# ---------------------------------------------------------------- 注册分流


@requires_lua
async def test_register_probes_and_pool_entry(routing_runtime):
    """探测通过 → 声明落表 + external 注册入 idle 池，sse_url 按 host 编址；
    探测不通 → pending 不报错（后续收养循环补）。"""
    rt = routing_runtime
    await _seed(rt)
    rt.k8s.unhealthy_pods.add("pod-b.as-svc.agentserver.svc")
    result = await _register(rt, _entry("pod-a"), _entry("pod-b"))

    assert [r["pod"] for r in result["registered"]] == ["pod-a"]
    assert result["registered"][0]["pod_sse_url"] == (
        "http://pod-a.as-svc.agentserver.svc:8080/sse")
    assert result["pending"] == [{"pod": "pod-b", "reason": "probe_failed"}]
    assert await rt.rm_state.declared_names(SCOPE) == ["pod-a", "pod-b"]
    assert await rt.rm_state.idle_pods(SCOPE) == ["pod-a"]
    info = await rt.rm_state.pod_info("pod-a")
    assert info["external"] == "1"
    assert info["pod_ip"] == "pod-a.as-svc.agentserver.svc"
    assert info["health_path"] == "/health"
    # 全程零 K8s 调用：不 deploy、不 delete、不 get（外部 Pod 不在 fake 集群）
    assert rt.k8s.deployed_specs == []
    assert rt.k8s.deleted == []


@requires_lua
async def test_register_idempotent_already_registered(routing_runtime):
    """重复注册幂等：已注册且探测通过的 Pod 不重放（防在用 Pod 被误标
    空闲）；同名重注册覆盖声明。"""
    rt = routing_runtime
    await _seed(rt)
    first = await _register(rt, _entry("pod-a"))
    assert first["already_registered"] == []
    # 占用一个会话 → pod-a 忙（不在 idle）
    r = await rt.route("sess_1")
    assert r["pod_id"] == "pod-a"
    second = await _register(rt, _entry("pod-a"))
    assert second["already_registered"] == ["pod-a"]
    assert second["registered"] == []
    # 忙 Pod 未被 idle_flag=1 重放污染
    assert await rt.rm_state.idle_pods(SCOPE) == []


@requires_lua
async def test_register_validation_errors(routing_runtime, runtime):
    """必填校验：缺 host / 空列表 / 名字带非法字符 / full 模式调用 / scope
    无模板（未 config_sync）→ 400 VALIDATION。"""
    rt = routing_runtime
    await _seed(rt)
    with pytest.raises(InvalidParams):
        await _register(rt, {"name": "pod-a"})            # 缺 host
    with pytest.raises(InvalidParams):
        await _register(rt, {"host": "h.as-svc"})          # 缺 name
    with pytest.raises(InvalidParams):
        await _register(rt)                                # 空
    with pytest.raises(InvalidParams):
        await _register(rt, {"name": "bad{pod}", "host": "h"})
    with pytest.raises(InvalidParams):
        await _register(rt, {"name": "pod-ok", "host": "http://x/y"})
    # full 模式(runtime fixture)下接口整体不可用
    with pytest.raises(InvalidParams):
        await runtime.rm_facade.register_pods(SCOPE, [_entry("pod-a")])
    # routing 模式但 scope 从未 config_sync（无模板可推导契约）
    rt_fresh = routing_runtime
    with pytest.raises(InvalidParams):
        await rt_fresh.rm_facade.register_pods(
            "scope-unknown", [_entry("pod-a")])


# ---------------------------------------------------------------- 分配/池空


@requires_lua
async def test_route_uses_declared_capacity_beyond_derived_max(routing_runtime):
    """注册容量即容量：声明 3 个 Pod 超过派生 max_pods=2（sc=10/pc=5），
    10 个会话全部落位（哨兵旁路 SM 第 7 步判满；sc/pc 闸门照常）。"""
    rt = routing_runtime
    await _seed(rt, scope_concurrency=10, pod_concurrency=5)  # max_pods=2
    await _register(rt, *(_entry(f"pod-{i}") for i in range(3)))
    for i in range(10):
        r = await rt.route(f"sess_{i}")
        assert r["pod_id"].startswith("pod-")
        assert r["pod_sse_url"].startswith("http://pod-")
    assert await rt.sm_state.scope_session_count(SCOPE) == 10
    # 每 Pod ≤ pc=5
    for pod in await rt.sm_state.scope_pod_ids(SCOPE):
        n = len(await rt.sm_state.redis.smembers(
            rt.sm_state.k.pod_sessions(SCOPE, pod)))
        assert n <= 5
    assert rt.k8s.deployed_specs == []       # 全程零自建


@requires_lua
async def test_pool_exhausted_fast_fail_no_deploy(routing_runtime):
    """池空快失败：全忙且无 idle → NoPodAvailable（外部供给措辞），绝不
    触发 deploy；deploying 占位不残留。"""
    rt = routing_runtime
    await _seed(rt, scope_concurrency=5, pod_concurrency=1)  # max_pods=5
    await _register(rt, _entry("pod-a"))
    r = await rt.route("sess_1")
    assert r["pod_id"] == "pod-a"
    with pytest.raises(NoPodAvailable) as exc_info:
        await rt.route("sess_2")
    assert "外部供给池" in str(exc_info.value)
    assert rt.k8s.deployed_specs == []
    assert await rt.rm_state.deploying_count(SCOPE) == 0


# ---------------------------------------------------------------- 判死/收养


@requires_lua
async def test_probe_death_purges_without_delete_then_adopt(routing_runtime):
    """唯一死亡信号=HTTP 探测：连续 2 次失败判死 → 解注册(零物理删) + 会话
    驱逐；恢复健康后 reconcile 收养 → 再 route 落同名 Pod（自愈闭环）。"""
    rt = routing_runtime
    await _seed(rt, scope_concurrency=4, pod_concurrency=1)
    await _register(rt, _entry("pod-a"), _entry("pod-b"))
    r = await rt.route("sess_1")
    victim = r["pod_id"]                  # 取暖迭代无序,按实际落位者驱动
    host = f"{victim}.as-svc.agentserver.svc"
    rt.k8s.unhealthy_pods.add(host)

    await rt.rm_sweeper.watch_once()      # 第 1 次失败
    assert victim in await rt.rm_state.all_pod_ids()
    await rt.rm_sweeper.watch_once()      # 第 2 次失败 → 判死
    assert victim not in await rt.rm_state.all_pod_ids()
    assert rt.k8s.deleted == []           # 零物理删（deregister-only）
    # 会话被驱逐（touch False = gateway 回退重 route）
    touched, _ = await rt.sm_state.touch("sess_1", now_ts(), 60)
    assert touched is False

    # StatefulSet 同名重建：恢复健康 → reconcile 收养 → 重新入池
    rt.k8s.unhealthy_pods.discard(host)
    adopted, pending = await rt.rm_orchestrator.adopt_declared(SCOPE)
    assert (adopted, pending) == (1, 0)
    r2 = await rt.route("sess_2")
    assert r2["pod_id"] == victim         # 同名恢复分配


@requires_lua
async def test_adopt_picks_up_pending_registration(routing_runtime):
    """注册时未就绪(pending)的 Pod：后台收养循环在健康后自动补注册。"""
    rt = routing_runtime
    await _seed(rt)
    host = "pod-late.as-svc.agentserver.svc"
    rt.k8s.unhealthy_pods.add(host)
    result = await _register(rt, _entry("pod-late"))
    assert result["pending"]
    rt.k8s.unhealthy_pods.discard(host)
    adopted, _ = await rt.rm_orchestrator.adopt_declared(SCOPE)
    assert adopted == 1
    r = await rt.route("sess_1")
    assert r["pod_id"] == "pod-late"


@requires_lua
async def test_watch_skips_k8s_phase_check_for_external(routing_runtime, caplog):
    """外部 Pod 走纯探测路径：不在 fake 集群里（get 必 None）也不判死——
    若误走 get 相位检查，会因 NotFound 每拍 PURGE。"""
    rt = routing_runtime
    await _seed(rt)
    await _register(rt, _entry("pod-a"))
    await rt.rm_sweeper.watch_once()
    await rt.rm_sweeper.watch_once()
    # 未加 unhealthy → 探测通过 → 仍在池
    assert "pod-a" in await rt.rm_state.all_pod_ids()


# ---------------------------------------------------------------- 注销/cleanup


@requires_lua
async def test_deregister_evicts_sessions_and_never_deletes(routing_runtime):
    """注销：驱逐在途会话 + 解声明/解注册，零物理删；幂等。"""
    rt = routing_runtime
    await _seed(rt)
    await _register(rt, _entry("pod-a"), _entry("pod-b"))
    victim = (await rt.route("sess_1"))["pod_id"]   # 取暖迭代无序,按实际落位者
    other = "pod-b" if victim == "pod-a" else "pod-a"
    result = await rt.rm_facade.deregister_pods(SCOPE, [victim])
    assert result == {"deregistered": [victim], "evicted_sessions": 1}
    assert await rt.rm_state.pod_info(victim) == {}
    assert await rt.rm_state.declared_names(SCOPE) == [other]
    assert rt.k8s.deleted == []
    touched, _ = await rt.sm_state.touch("sess_1", now_ts(), 60)
    assert touched is False
    # 幂等重放
    result2 = await rt.rm_facade.deregister_pods(SCOPE, [victim])
    assert result2["evicted_sessions"] == 0


@requires_lua
async def test_cleanup_rejected_in_routing_mode(routing_runtime):
    rt = routing_runtime
    with pytest.raises(InvalidParams):
        await rt.rm_facade.cleanup(namespace=None, label_selector=None)


# ---------------------------------------------------------------- 版本豁免


@requires_lua
async def test_config_refresh_not_blocked_and_pods_survive(routing_runtime):
    """config_refresh：外部 Pod 不卡 409 闸门；refresh 后仍可复用分配
    （代次豁免）。"""
    rt = routing_runtime
    await _seed(rt, scope_concurrency=4, pod_concurrency=2)
    await _register(rt, _entry("pod-a"))
    r = await rt.route("sess_1")
    assert r["pod_id"] == "pod-a"
    # 会话结束 → pod 转 idle（回暖池后 refresh 复用走 RM acquire reuse）
    await rt.sm_state.evict("sess_1")
    await rt.sm_sweeper.sweep_once()
    await asyncio.sleep(0.05)
    await rt.config_store.config_refresh({})
    # refresh 后同名外部 Pod 仍可分配（generation 豁免，不被判 lagged）
    r2 = await rt.route("sess_2")
    assert r2["pod_id"] == "pod-a"


@requires_lua
async def test_config_sync_keeps_external_in_candidates(routing_runtime):
    """A 类变更（换镜像）：SM 软摘除豁免点⑤——外部 Pod 留在候选集继续接
    流量（物理规格由客户 StatefulSet 自管，版本语义不适用）。"""
    rt = routing_runtime
    await _seed(rt)
    await _register(rt, _entry("pod-a"))
    await rt.route("sess_1")
    # A 类变更：改 agent_image → deploy_ver 变
    await rt.seed_template(agent_image="agentserver:2.0")
    assert "pod-a" in await rt.sm_state.scope_pod_ids(SCOPE)
    # 存量会话继续持有;新会话仍落外部 Pod（RM 复用豁免 + 候选未摘）
    r2 = await rt.route("sess_2")
    assert r2["pod_id"] == "pod-a"


@requires_lua
async def test_scope_deletion_drops_declared_and_drains(routing_runtime):
    """scope 删除：drop_declared 清声明 → 外部 Pod 失去豁免 → 既有 gen-lag
    排空回收（session_ttl 窗口，测试调小到 1s；只解绑零物理删）。"""
    rt = routing_runtime
    await _seed(rt, session_ttl=1, pod_concurrency=1)
    await _register(rt, _entry("pod-a"))
    await rt.route("sess_1")
    # 全量下发空载荷 = 删除该 scope（扩散③：bump + min_idle=0 + drop_declared）
    await rt.config_store.config_sync({"containers": [], "templates": [],
                                       "scopes": []})
    assert await rt.rm_state.declared_names(SCOPE) == []
    # 会话到期 + drain 窗口(1s)过后 reclaim 收尾
    past = now_ts() - 10
    await rt.sm_state.redis.hset(
        rt.sm_state.k.session("sess_1"), "expiry", str(past))
    await rt.sm_state.redis.zadd(
        rt.sm_state.k.session_expiry(), {"sess_1": past})
    await rt.sm_sweeper.sweep_once()
    await asyncio.sleep(1.2)
    await rt.rm_sweeper.reclaim_once()
    assert "pod-a" not in await rt.rm_state.all_pod_ids()
    assert rt.k8s.deleted == []           # 排空回收也不物理删


# ---------------------------------------------------------------- 哨兵（状态层）


async def test_route_place_sentinel_bypasses_max_pods_gate(routing_runtime):
    """LUA_ROUTE_PLACE 第 7 步：模板 max_pods=1 时候选打满即 scope_full；
    哨兵值下放行 need_acquire（RM 复用 idle 外部 Pod 的入口）。"""
    rt = routing_runtime
    from agent_runtime.session_manager.state import SessionState

    sm = SessionState(rt.redis)
    scope = SCOPE
    now = now_ts()
    # 播种两个满载候选 Pod（pc=1）+ scope 闸门余量（sc=10）
    for pod in ("pod-1", "pod-2"):
        await sm.register_pod(scope, pod, f"http://{pod}:8080/sse", "v1")
        await sm.redis.sadd(sm.k.pod_sessions(scope, pod), f"stub_{pod}")
    await sm.redis.sadd(sm.k.scope_sessions(scope), "stub_1", "stub_2")
    action, _ = await sm.route_place(
        session_id="sess_new", scope_id=scope, expiry_ts=now + 60,
        session_ttl=60, scope_concurrency=10, pod_concurrency=1,
        max_pods=1, now=now)
    assert action == "scope_full"         # 模板值：候选 2 ≥ max_pods 1
    action, _ = await sm.route_place(
        session_id="sess_new", scope_id=scope, expiry_ts=now + 60,
        session_ttl=60, scope_concurrency=10, pod_concurrency=1,
        max_pods=ROUTING_MAX_PODS_SENTINEL, now=now)
    assert action == "need_acquire"       # 哨兵：旁路判满，交 RM 复用


# ---------------------------------------------------------------- 无 Lua 双模式


async def test_routing_core_flow_no_lua(db_handler, redis_client, k8s):
    """无 Lua 兼容模式下的核心链等价：注册→分配→复用（ACQUIRE 豁免走
    WATCH/MULTI/EXEC 路径）。"""
    rt = Runtime(db_handler, redis_client, k8s,
                 use_lua=False, deploy_mode="routing")
    await rt.seed_template(scope_concurrency=4, pod_concurrency=2)
    result = await _register(rt, _entry("pod-a"))
    assert [r["pod"] for r in result["registered"]] == ["pod-a"]
    assert (await rt.route("sess_1"))["pod_id"] == "pod-a"
    assert (await rt.route("sess_2"))["pod_id"] == "pod-a"   # pc=2 同 Pod
    # 会话释放 → idle → 再分配复用（no-Lua reuse 分支的豁免命中）
    await rt.sm_state.evict("sess_1")
    await rt.sm_state.evict("sess_2")
    await rt.sm_sweeper.sweep_once()
    await asyncio.sleep(0.05)
    assert await rt.rm_state.idle_pods(SCOPE) == ["pod-a"]
    assert (await rt.route("sess_4"))["pod_id"] == "pod-a"
