# coding: utf-8
"""RM 状态层单测（M2）：LUA_ACQUIRE / REGISTER / RELEASE / PURGE。

覆盖 HLD 场景 I（取暖 Pod / deploy 占位 / 封顶 / deploy_ver 过滤）、
K（reclaim 前置状态）、G/J（purge 清理）的 Redis 侧断言。
"""

from __future__ import annotations

import pytest

from agent_runtime.util import scope_id_of
from tests.conftest import requires_lua

SCOPE = scope_id_of("grp", "bot")
NOW = 1_000_000


@pytest.fixture
async def scope_pool(rm_state):
    """预置 scope 池：config（min_idle=1 / max_pods=2 / pod_ttl=300）+ 暖 Pod pod_w。"""
    await rm_state.save_scope_config(
        SCOPE, {"min_idle_pods": 1, "max_pods": 2, "pod_ttl": 300, "deploy_ver": "ver1"}
    )
    await rm_state.register_pod(
        "pod_w", SCOPE, "http://10.0.0.1:8080/sse", "10.0.0.1", "default",
        "ver1", "token-warmup", idle_flag=True, now=NOW,
    )
    return rm_state


# ---------------------------------------------------------------- 场景 I：acquire


@requires_lua
async def test_acquire_no_config(rm_state):
    action, _, _ = await rm_state.acquire(SCOPE, "ver1", "t1")
    assert action == "no_config"


@requires_lua
async def test_acquire_reuses_warm_pod(scope_pool):
    """idle 池有匹配版本暖 Pod → 复用（零部署），移出 idle + 清计时。"""
    action, pod, url = await scope_pool.acquire(SCOPE, "ver1", "t1")
    assert (action, pod, url) == ("reuse", "pod_w", "http://10.0.0.1:8080/sse")
    assert await scope_pool.redis.sismember(
        scope_pool.k.scope_idle(SCOPE), "pod_w"
    ) == 0
    assert await scope_pool.redis.exists(scope_pool.k.pod_idle_since("pod_w")) == 0


@requires_lua
async def test_acquire_skips_stale_version_warm_pod(scope_pool):
    """场景 M：deploy_ver 不匹配的暖 Pod 不外发（留在 idle 池按 pod_ttl 回收）。"""
    action, pod, _ = await scope_pool.acquire(SCOPE, "ver2", "t1")
    # 无匹配暖 Pod、未达 max_pods → 占位待 deploy（老暖 Pod 留在 idle）
    assert action == "need_deploy"
    assert await scope_pool.redis.sismember(scope_pool.k.scope_idle(SCOPE), "pod_w")
    assert await scope_pool.clear_deploy_token(SCOPE, "t1") is None  # 错误路径清占位


@requires_lua
async def test_acquire_need_deploy_occupies_token(scope_pool):
    """先取走暖 Pod，再 acquire → need_deploy 且占位计入 max_pods 判定。"""
    await scope_pool.acquire(SCOPE, "ver1", "t0")  # 取走 pod_w
    action, _, _ = await scope_pool.acquire(SCOPE, "ver1", "t1")
    assert action == "need_deploy"
    assert await scope_pool.deploying_count(SCOPE) == 1
    # deploying 占位计入封顶判定：ZCARD(0) + SCARD(1) >= max_pods(1 时封顶)
    await scope_pool.save_scope_config(SCOPE, {"max_pods": 1})
    action2, _, _ = await scope_pool.acquire(SCOPE, "ver1", "t2")
    assert action2 == "max_reached"


@requires_lua
async def test_acquire_max_reached(rm_state):
    await rm_state.save_scope_config(SCOPE, {"max_pods": 1})
    await rm_state.register_pod(
        "pod_1", SCOPE, "http://x/sse", "ip", "ns", "ver1", "tok", False, NOW
    )
    action, _, _ = await rm_state.acquire(SCOPE, "ver1", "t1")
    assert action == "max_reached"


# ---------------------------------------------------------------- REGISTER


@requires_lua
async def test_register_pod_in_use(scope_pool):
    await scope_pool.register_pod(
        "pod_2", SCOPE, "http://10.0.0.2:8080/sse", "10.0.0.2", "default",
        "ver1", "tok-1", idle_flag=False, now=NOW + 5,
    )
    assert await scope_pool.pod_count(SCOPE) == 2
    assert await scope_pool.deploying_count(SCOPE) == 0       # 占位已清
    assert await scope_pool.redis.sismember(scope_pool.k.pods_all(), "pod_2")
    info = await scope_pool.pod_info("pod_2")
    assert info["scope_id"] == SCOPE and info["pod_ip"] == "10.0.0.2"
    assert info["deploy_ver"] == "ver1"
    # 非热备：不入 idle 池、无 idle_since 计时
    assert await scope_pool.redis.exists(scope_pool.k.pod_idle_since("pod_2")) == 0
    assert not await scope_pool.idle_pods(SCOPE) or "pod_2" not in await scope_pool.idle_pods(SCOPE)


@requires_lua
async def test_register_pod_idle_flag(scope_pool):
    """热备注册（autoscale 场景 H）：入 idle 池 + idle_since（不变量 5）。"""
    await scope_pool.register_pod(
        "pod_hot", SCOPE, "http://10.0.0.3:8080/sse", "10.0.0.3", "default",
        "ver1", "tok-2", idle_flag=True, now=NOW + 10,
    )
    assert await scope_pool.redis.sismember(scope_pool.k.scope_idle(SCOPE), "pod_hot")
    assert await scope_pool.idle_since("pod_hot") == NOW + 10


# ---------------------------------------------------------------- RELEASE（场景 D/K 入口）


@requires_lua
async def test_release_transitions_to_idle(scope_pool):
    ok = await scope_pool.release("pod_w", SCOPE, now=NOW + 30)
    assert ok is True
    assert await scope_pool.idle_since("pod_w") == NOW + 30


@requires_lua
async def test_release_idempotent(scope_pool):
    await scope_pool.release("pod_w", SCOPE, now=NOW + 30)
    await scope_pool.release("pod_w", SCOPE, now=NOW + 40)  # 重复/延迟抵达无副作用
    assert await scope_pool.redis.scard(scope_pool.k.scope_idle(SCOPE)) == 1


# ---------------------------------------------------------------- PURGE（场景 G/J/K 清理）


@requires_lua
async def test_purge_clears_all_rm_state(scope_pool):
    scope = await scope_pool.purge("pod_w")
    assert scope == SCOPE
    assert await scope_pool.redis.exists(scope_pool.k.pod_info("pod_w")) == 0
    assert await scope_pool.redis.exists(scope_pool.k.pod_idle_since("pod_w")) == 0
    assert await scope_pool.redis.sismember(scope_pool.k.pods_all(), "pod_w") == 0
    assert await scope_pool.pod_count(SCOPE) == 0
    assert await scope_pool.redis.scard(scope_pool.k.scope_idle(SCOPE)) == 0


@requires_lua
async def test_purge_unknown_pod(rm_state):
    assert await rm_state.purge("ghost") == ""    # 幂等


# ---------------------------------------------------------------- scope 枚举


@requires_lua
async def test_known_scope_ids_scan(rm_state):
    await rm_state.save_scope_config("scopeA", {"max_pods": 1})
    await rm_state.save_scope_config("scopeB", {"max_pods": 2})
    assert await rm_state.known_scope_ids() == ["scopeA", "scopeB"]


# ---------------------------------------------------------------- follower 等待室


@requires_lua
async def test_deploy_follower_gate_admits_up_to_cap(rm_state):
    """准入上限 pc-1：第 max+1 个自退（ZADD 先行 + ZCARD 超限自退，原子）。"""
    assert await rm_state.try_add_deploy_follower(
        SCOPE, "f1", max_followers=1, deadline=NOW + 60, now=NOW) is True
    assert await rm_state.try_add_deploy_follower(
        SCOPE, "f2", max_followers=1, deadline=NOW + 60, now=NOW) is False
    assert await rm_state.deploy_follower_count(SCOPE) == 1


@requires_lua
async def test_deploy_follower_gate_concurrent_burst_respects_cap(rm_state):
    """并发同时准入不超过上限（回归形态：同 LUA_WAITER_GATE 的纪律）。"""
    import asyncio

    results = await asyncio.gather(*[
        rm_state.try_add_deploy_follower(
            SCOPE, f"f-{i}", max_followers=2, deadline=NOW + 60, now=NOW)
        for i in range(6)
    ])
    assert results.count(True) == 2
    assert await rm_state.deploy_follower_count(SCOPE) == 2


@requires_lua
async def test_deploy_follower_gate_purges_expired_members(rm_state):
    """崩溃兜底：score=deadline 过期的成员在下次准入时被原子清掉，不占名额。"""
    assert await rm_state.try_add_deploy_follower(
        SCOPE, "f-dead", max_followers=1, deadline=NOW + 10, now=NOW) is True
    # 时间前进到 f-dead 的 deadline 之后（进程崩溃遗留）
    assert await rm_state.try_add_deploy_follower(
        SCOPE, "f-new", max_followers=1, deadline=NOW + 100, now=NOW + 11) is True
    assert await rm_state.deploy_follower_count(SCOPE) == 1


@requires_lua
async def test_remove_deploy_follower(rm_state):
    """正常退出/错误路径退出清成员（finally 纪律）。"""
    assert await rm_state.try_add_deploy_follower(
        SCOPE, "f1", max_followers=1, deadline=NOW + 60, now=NOW) is True
    await rm_state.remove_deploy_follower(SCOPE, "f1")
    assert await rm_state.deploy_follower_count(SCOPE) == 0
    await rm_state.remove_deploy_follower(SCOPE, "f1")   # 幂等


@requires_lua
async def test_lock_held(rm_state):
    key = rm_state.k.lock_deploy(SCOPE)
    assert await rm_state.lock_held(key) is False
    await rm_state.redis.set(key, "leader", ex=30)
    assert await rm_state.lock_held(key) is True


@requires_lua
async def test_pod_ids_lists_scope_pods(scope_pool):
    assert await scope_pool.pod_ids(SCOPE) == ["pod_w"]
