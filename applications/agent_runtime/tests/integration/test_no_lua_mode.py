# coding: utf-8
"""无 Lua 模式（AGENT_RUNTIME_REDIS_LUA=off）组件全链路集成测试。

与 test_route_flow 同一组场景面，但 State 门面 use_lua=False（WATCH/
MULTI/EXEC 路径）——验证 route/touch/rebind/扩缩容/sweeper/config_sync
（含 SM 锁 unlock/refresh_lock 走 watch_cas）全链在禁 EVAL 语义下等价。
不含 lupa 依赖：无 Lua 的 CI 亦全覆盖。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.errors import ScopeFull
from agent_runtime.util import now_ts

SCOPE = "scope-main"


async def test_route_full_flow_no_lua(runtime_no_lua):
    """场景 A/B/C：首 route 触发 deploy → 登记 → 放置；亲和续期；打包+扩容。"""
    rt = runtime_no_lua
    await rt.seed_template()
    r1 = await rt.route("sess_1")
    assert r1["pod_id"].startswith("agentserver-")
    assert await rt.sm_state.scope_pod_ids(SCOPE) == [r1["pod_id"]]
    assert await rt.rm_state.pod_count(SCOPE) == 1
    # 亲和续期（refresh 分支）
    r2 = await rt.route("sess_1")
    assert r2["pod_id"] == r1["pod_id"]
    # 打包 + 扩容（pod_concurrency=2）
    r3 = await rt.route("sess_2")
    r4 = await rt.route("sess_3")
    assert r3["pod_id"] == r1["pod_id"]
    assert r4["pod_id"] != r1["pod_id"]
    assert len(await rt.sm_state.scope_pod_ids(SCOPE)) == 2


async def test_route_scope_full_fast_fail_no_lua(runtime_no_lua):
    """场景 F：Pod 全满 + 达 max_pods → 立即 503 ScopeFull。"""
    rt = runtime_no_lua
    await rt.seed_template(scope_concurrency=4, pod_concurrency=2)  # max_pods=2
    await rt.route("sess_1")
    await rt.route("sess_2")
    await rt.route("sess_3")
    await rt.route("sess_4")
    with pytest.raises(ScopeFull):
        await rt.route("sess_5")


async def test_touch_and_rebind_via_sweeper_no_lua(runtime_no_lua):
    """touch 保活 + 到期 sweeper pass（evict/惰性回收）+ 空 Pod pass（idle 链）。"""
    rt = runtime_no_lua
    await rt.seed_template()
    r1 = await rt.route("sess_1")
    ok, _ = await rt.sm_state.touch("sess_1", now_ts() + 1)
    assert ok is True
    # 到期 pass：把 expiry 拨到过去（只调 TTL 键值，不回拨时钟——
    # e2e 硬标准允许调小 TTL）后跑 sweeper
    past = now_ts() - 10
    await rt.sm_state.redis.hset(
        rt.sm_state.k.session("sess_1"), "expiry", str(past))
    await rt.sm_state.redis.zadd(
        rt.sm_state.k.session_expiry(), {"sess_1": past})
    await rt.sm_sweeper.sweep_once()
    assert await rt.sm_state.session_hash("sess_1") == {}
    assert await rt.sm_state.scope_session_count(SCOPE) == 0
    # 空 Pod pass：候选集摘除 + idle_consider 转暖池（fire-and-forget，让出循环）
    pod = r1["pod_id"]
    await rt.sm_sweeper.sweep_once()
    await asyncio.sleep(0.05)
    assert pod not in await rt.sm_state.scope_pod_ids(SCOPE)
    idle = await rt.rm_state.idle_pods(SCOPE)
    assert pod in idle
    assert await rt.rm_state.idle_since(pod) > 0


async def test_concurrent_route_no_oversell_no_lua(runtime_no_lua):
    """并发 route 打满闸门不超卖（scope_cc/pod_cc/max_pods）。"""
    rt = runtime_no_lua
    await rt.seed_template(scope_concurrency=10, pod_concurrency=5)  # max_pods=2
    results: list = []

    async def one(i):
        try:
            r = await rt.route(f"sess_{i}")
            results.append(("ok", r["pod_id"]))
        except ScopeFull:
            results.append(("full", ""))

    await asyncio.gather(*(one(i) for i in range(30)))
    ok = [r for r in results if r[0] == "ok"]
    assert len(ok) == 10
    assert await rt.sm_state.scope_session_count(SCOPE) == 10
    pods = await rt.sm_state.scope_pod_ids(SCOPE)
    for p in pods:
        n = len(await rt.sm_state.redis.smembers(
            rt.sm_state.k.pod_sessions(SCOPE, p)))
        assert n <= 5, (p, n)


async def test_config_sync_locks_no_lua(runtime_no_lua):
    """config_sync 串行化锁（unlock/refresh_lock 走 watch_cas）可正常推进。"""
    rt = runtime_no_lua
    await rt.seed_template()
    await rt.seed_template(template_id="tpl-2")
    assert await rt.sm_state.try_lock(
        rt.sm_state.k.lock_config_sync(), 30, "tok-a")
    # 非 owner 不解锁
    await rt.sm_state.unlock(rt.sm_state.k.lock_config_sync(), "tok-b")
    assert await rt.sm_state.redis.get(
        rt.sm_state.k.lock_config_sync()) == b"tok-a"
    # owner 续期 + 解锁
    assert await rt.sm_state.refresh_lock(
        rt.sm_state.k.lock_config_sync(), 30, "tok-a") is True
    await rt.sm_state.unlock(rt.sm_state.k.lock_config_sync(), "tok-a")
    assert await rt.sm_state.redis.get(
        rt.sm_state.k.lock_config_sync()) is None
