# coding: utf-8
"""RM 状态层双模式（Lua / 无 Lua）等价性套件。

parametrize("use_lua", [True, False]) 双跑同一组不变量断言：
- acquire：no_config / need_deploy 占位防并发超配 / 暖 Pod 复用（deploy_ver +
  generation 双匹配）/ max_reached 闸门（占位计入）/ drain surge
- placeholder：autoscale 占位（同闸门，不碰 idle 池）
- register：generation 服务端烙印 + 清占位 + 热备入 idle
- release：幂等不刷新计时 / 幽灵守卫（PURGE 后不复活）
- pop_idle / purge 幂等；deploy_follower_gate 加前判定不超收
- reconcile 自愈：无计时 idle 成员补 SET NX（无 Lua 崩溃窗口兜底）
"""

from __future__ import annotations

import pytest

from agent_runtime.resource_manager.state import ResourceState
from agent_runtime.util import now_ts

from tests.conftest import requires_lua  # noqa: F401

_USE_LUA = [
    pytest.param(True, id="lua", marks=requires_lua),
    pytest.param(False, id="no-lua"),
]


@pytest.fixture(params=_USE_LUA)
def rm(request, redis_client):  # noqa: F811
    return ResourceState(redis_client, use_lua=request.param)


NOW = 1_000_000


async def _seed_scope(rm, **cfg):
    base = {"max_pods": 3, "pod_ttl": 300, "min_idle_pods": 0}
    base.update(cfg)
    await rm.save_scope_config("sc", base)


async def test_acquire_gate_placeholder_and_reuse(rm):
    await _seed_scope(rm)
    # no_config
    act, _, _ = await rm.acquire("nope", "v1", "t0")
    assert act == "no_config"
    # need_deploy + 占位计入闸门（max_pods=3 → 第 4 个占位 max_reached）
    assert (await rm.acquire("sc", "v1", "t1"))[0] == "need_deploy"
    assert (await rm.acquire("sc", "v1", "t2"))[0] == "need_deploy"
    assert (await rm.acquire("sc", "v1", "t3"))[0] == "need_deploy"
    assert (await rm.acquire("sc", "v1", "t4"))[0] == "max_reached"
    assert await rm.deploying_count("sc") == 3
    # register 清占位（t1）→ pods=1 + deploying={t2,t3} = 3 仍满容
    await rm.register_pod("p1", "sc", "http://p1", "ip", "ns", "v1",
                          "t1", False, NOW, sse_port=1, health_path="/h")
    assert await rm.deploying_count("sc") == 2
    assert (await rm.acquire("sc", "v1", "t5"))[0] == "max_reached"
    await rm.clear_deploy_token("sc", "t2")   # 腾一个占位 → total=2 < 3
    assert (await rm.acquire("sc", "v1", "t5"))[0] == "need_deploy"
    await rm.clear_deploy_token("sc", "t3")
    await rm.clear_deploy_token("sc", "t5")
    # 复用：ver+gen 双匹配
    await rm.release("p1", "sc", NOW)
    act, pod, url = await rm.acquire("sc", "v1", "t6")
    assert (act, pod, url) == ("reuse", "p1", "http://p1")
    assert await rm.idle_pods("sc") == [] and await rm.idle_since("p1") == 0
    # ver 不匹配不复用
    await rm.release("p1", "sc", NOW)
    act, _, _ = await rm.acquire("sc", "v9", "t7")
    assert act == "need_deploy"
    await rm.clear_deploy_token("sc", "t7")
    # gen 不匹配不复用
    await rm.bump_generation("sc")
    act, _, _ = await rm.acquire("sc", "v1", "t8")
    assert act == "need_deploy"
    await rm.clear_deploy_token("sc", "t8")


async def test_acquire_drain_surge(rm):
    await _seed_scope(rm, max_pods=1)
    assert (await rm.acquire("sc", "v1", "t1"))[0] == "need_deploy"
    assert (await rm.acquire("sc", "v1", "t2"))[0] == "max_reached"
    # 排空纪元 → cap+1 放行（sunset drain 优雅排空配套头寸）
    await rm.set_drain_until("sc", NOW + 600, 700)
    assert (await rm.acquire("sc", "v1", "t3"))[0] == "need_deploy"
    await rm.clear_drain_until("sc")


async def test_placeholder_same_gate_no_idle(rm):
    await _seed_scope(rm, max_pods=2)
    await rm.register_pod("p1", "sc", "http://p1", "ip", "ns", "v1",
                          "none", True, NOW, sse_port=1, health_path="/h")
    assert await rm.idle_pods("sc") == ["p1"]
    # 占位计入闸门（pods=1, max=2 → 1 个占位后满容）
    assert await rm.deploy_placeholder("sc", "ph1") == "need_deploy"
    assert await rm.deploy_placeholder("sc", "ph2") == "max_reached"
    # placeholder 不碰 idle 池（热备不被补位消耗）
    assert await rm.idle_pods("sc") == ["p1"]
    assert await rm.idle_since("p1") == NOW


async def test_register_generation_branding_and_idle(rm):
    await _seed_scope(rm)
    await rm.bump_generation("sc")   # gen=1
    await rm.bump_generation("sc")   # gen=2
    await rm.register_pod("p1", "sc", "http://p1", "ip", "ns", "v1",
                          "tok", True, NOW, sse_port=8086, health_path="/hp")
    info = await rm.pod_info("p1")
    assert info["generation"] == "2"          # 注册时刻代次烙印
    assert info["phase"] == "created"
    assert info["sse_port"] == "8086" and info["health_path"] == "/hp"
    assert info["created_ts"] == str(NOW)
    assert await rm.idle_pods("sc") == ["p1"]  # idle_flag 热备入池
    assert await rm.idle_since("p1") == NOW
    # 非 idle 注册
    await rm.register_pod("p2", "sc", "http://p2", "ip", "ns", "v1",
                          "tok", False, NOW)
    assert "p2" not in await rm.idle_pods("sc")
    assert await rm.pod_count("sc") == 2


async def test_release_idempotent_timer_and_ghost_guard(rm):
    await _seed_scope(rm)
    await rm.register_pod("p1", "sc", "http://p1", "ip", "ns", "v1",
                          "t", False, NOW)
    # 首次转 idle 起计时
    assert await rm.release("p1", "sc", NOW) is True
    assert await rm.idle_since("p1") == NOW
    # 重放不刷新计时（reclaim 的 aged≥pod_ttl 才可能达成）
    assert await rm.release("p1", "sc", NOW + 999) is True
    assert await rm.idle_since("p1") == NOW
    # PURGE 后重放不复活幽灵
    await rm.purge("p1")
    assert await rm.release("p1", "sc", NOW) is False
    assert await rm.idle_pods("sc") == []
    # 弹出后再转 idle 属新空闲期，重新计时
    await rm.register_pod("p1", "sc", "http://p1", "ip", "ns", "v1",
                          "t", False, NOW)
    await rm.release("p1", "sc", NOW)
    await rm.pop_idle("p1", "sc")
    await rm.release("p1", "sc", NOW + 50)
    assert await rm.idle_since("p1") == NOW + 50


async def test_pop_idle_and_purge_idempotent(rm):
    await _seed_scope(rm)
    await rm.register_pod("p1", "sc", "http://p1", "ip", "ns", "v1",
                          "t", True, NOW)
    # pop_idle 幂等（非 idle 成员双 no-op）
    await rm.pop_idle("p1", "sc")
    await rm.pop_idle("p1", "sc")
    assert await rm.idle_pods("sc") == [] and await rm.idle_since("p1") == 0
    # purge 返回 scope + 幂等
    assert await rm.purge("p1") == "sc"
    assert await rm.pod_info("p1") == {}
    assert await rm.pod_count("sc") == 0
    assert await rm.all_pod_ids() == []
    assert await rm.purge("p1") == ""


async def test_deploy_follower_gate(rm):
    await _seed_scope(rm)
    assert await rm.try_add_deploy_follower("sc", "f1", 2, NOW + 300, NOW) is True
    assert await rm.try_add_deploy_follower("sc", "f2", 2, NOW + 300, NOW) is True
    assert await rm.try_add_deploy_follower("sc", "f3", 2, NOW + 300, NOW) is False
    assert await rm.deploy_follower_count("sc") == 2
    # 满员时过期成员不挤占在席者（先清 dead → f4 仍受 2 上限约束）
    await rm.redis.zadd(rm.k.scope_deploy_followers("sc"), {"dead": NOW - 1})
    assert await rm.try_add_deploy_follower("sc", "f4", 2, NOW + 300, NOW) is False
    # 腾出席位后过期成员被自清、可再入
    await rm.remove_deploy_follower("sc", "f2")
    await rm.redis.zadd(rm.k.scope_deploy_followers("sc"), {"dead": NOW - 1})
    assert await rm.try_add_deploy_follower("sc", "f4", 2, NOW + 300, NOW) is True
    members = {m.decode() for m in await rm.redis.zrange(
        rm.k.scope_deploy_followers("sc"), 0, -1)}
    assert members == {"f1", "f4"}  # dead 已清


async def test_concurrent_acquire_no_oversell(redis_client):  # noqa: F811
    """并发 acquire 占位不超 max_pods（no-lua WATCH 闸门）。"""
    import asyncio

    rm = ResourceState(redis_client, use_lua=False)
    await _seed_scope(rm, max_pods=4)
    results = []

    async def one(i):
        act, _, _ = await rm.acquire("sc", "v1", f"tok{i}")
        results.append(act)

    await asyncio.gather(*(one(i) for i in range(12)))
    assert results.count("need_deploy") == 4
    assert await rm.deploying_count("sc") == 4  # 占位恰满，无超配


async def test_reconcile_heals_missing_idle_since(redis_client, k8s):  # noqa: F811
    """无计时 idle 成员自愈（no-Lua release 崩溃窗口的 reconcile 兜底）。"""
    from agent_runtime.resource_manager.orchestrator import ResourceOrchestrator
    from agent_runtime.resource_manager.sweeper import ResourceSweeper

    class _AliveK8s(type(k8s)):
        async def get_pod(self, pod_id, namespace):
            return object()  # 绕开孤儿清理，聚焦自愈项

    rm = ResourceState(redis_client, use_lua=False)
    await _seed_scope(rm)
    await rm.register_pod("p1", "sc", "http://p1", "ip", "ns", "v1",
                          "t", True, NOW)
    # 模拟崩溃窗口：SADD idle 已落、SET idle_since 丢失
    await rm.redis.delete(rm.k.pod_idle_since("p1"))
    assert await rm.idle_since("p1") == 0

    alive = _AliveK8s()
    sweeper = ResourceSweeper(
        rm, alive, None, orchestrator=ResourceOrchestrator(rm, alive))
    await sweeper.reconcile_once()
    assert await rm.idle_since("p1") > 0
    assert await rm.idle_pods("sc") == ["p1"]
    # 已有计时的不刷新
    t = await rm.idle_since("p1")
    await sweeper.reconcile_once()
    assert await rm.idle_since("p1") == t


# ---------------------------------------------- routing 供给模式（2026-10,双模式）

async def _seed_external(rm, scope="sc", pod="pod-x", host="pod-x.svc",
                         ver="ver1", declared=True):
    """播种外部形态：scope config + declared 声明 + external 注册入 idle。"""
    await _seed_scope(rm, deploy_ver=ver)
    if declared:
        await rm.add_declared(scope, {
            pod: '{"host": "%s", "declared_ts": 1}' % host})
    await rm.register_pod(
        pod, scope, f"http://{host}:8080/sse", host, "default",
        ver, "", idle_flag=True, now=NOW, sse_port=8080,
        health_path="/health", external=True,
    )


async def test_declared_crud(rm):
    """声明表五操作：add/entries/names/remove/drop + scope 枚举（SCAN）。"""
    await rm.add_declared("sc", {"pod-x": '{"host": "h1", "declared_ts": 1}',
                                 "pod-y": '{"host": "h2", "declared_ts": 1}'})
    assert await rm.declared_names("sc") == ["pod-x", "pod-y"]
    entries = await rm.declared_entries("sc")
    assert entries["pod-x"] == '{"host": "h1", "declared_ts": 1}'
    assert await rm.declared_scope_ids() == ["sc"]
    assert await rm.remove_declared("sc", ["pod-y"]) == 1
    assert await rm.declared_names("sc") == ["pod-x"]
    assert await rm.drop_declared("sc") == 1
    assert await rm.declared_names("sc") == []
    # 幂等：drop 空表返回 0；scope 枚举收敛为空
    assert await rm.drop_declared("sc") == 0
    assert await rm.declared_scope_ids() == []


async def test_acquire_exempts_declared_from_version_filter(rm):
    """declared 成员豁免 ver/gen 过滤：版本与代次均不匹配仍可复用（外部
    Pod 版本无关，客户 StatefulSet 自管形态）。undeclared 不豁免。"""
    await _seed_external(rm)          # ver1 注册
    await rm.bump_generation("sc")    # 代次落后
    action, pod, url = await rm.acquire("sc", "ver2", "t1")
    assert (action, pod) == ("reuse", "pod-x")
    assert url == "http://pod-x.svc:8080/sse"
    # 失去声明资格 → 豁免解除，回到版本过滤（need_deploy 占位）：
    # pod-z 未声明注册、pod-x 撤销声明后回 idle 复位现场，两者均不豁免
    await rm.register_pod(
        "pod-z", "sc", "http://h3:8080/sse", "h3", "default",
        "ver1", "", idle_flag=True, now=NOW, external=True)
    await rm.remove_declared("sc", ["pod-x"])
    await rm.release("pod-x", "sc", NOW + 10)
    await rm.bump_generation("sc")
    action, pod, _ = await rm.acquire("sc", "ver2", "t2")
    assert action == "need_deploy"    # pod-z / pod-x 均 undeclared → 不豁免
    await rm.clear_deploy_token("sc", "t2")


async def test_register_external_flag_and_purge_keeps_declared(rm):
    """external=1 落 pod:info；PURGE 只清 Pod 记账、不动声明表（声明的
    删除权在 config_store 的 drop_declared / deregister_pods）。"""
    await _seed_external(rm)
    info = await rm.pod_info("pod-x")
    assert info.get("external") == "1"
    assert await rm.purge("pod-x") == "sc"
    assert await rm.pod_info("pod-x") == {}
    assert await rm.declared_names("sc") == ["pod-x"]


async def test_has_lagged_pods_ignores_declared(rm):
    """排空纪元判据豁免：declared 成员代次落后不构成 lagged（否则外部 Pod
    虚假触发 drain 纪元/reclaim 排空）；drop 后构成。"""
    await _seed_external(rm)
    await rm.bump_generation("sc")
    assert await rm.has_lagged_pods("sc") is False
    await rm.drop_declared("sc")
    assert await rm.has_lagged_pods("sc") is True
