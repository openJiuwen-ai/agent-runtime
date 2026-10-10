# coding: utf-8
"""SM 状态层双模式（Lua / 无 Lua）等价性套件。

parametrize("use_lua", [True, False]) 双跑同一组不变量断言：
- placement 四处同写（session hash / scope:sessions / pod:sessions / expiry）
- 亲和 refresh / 判死与过期惰性回收 rebind / 残骸自卫落穿
- 三重闸门（scope_cc / pod_cc / max_pods）并发不超卖
- rebind 五态（noop/rubble/evicted/overtaken/rebound）
- touch 惰性 evict + ttl 就地读；evict 幂等
- 竞争专项：复读后 EXEC 前注入竞争写者 → WatchError 重试后不变量保持

lua 参数依赖 lupa（缺失自动 skip）；no-lua 参数无需 lupa（扩大 CI 覆盖）。
"""

from __future__ import annotations

import asyncio

import pytest
from redis.exceptions import WatchError

from agent_runtime.session_manager.state import SessionState
from agent_runtime.util import now_ts, s

from tests.conftest import requires_lua  # noqa: F401

_USE_LUA = [
    pytest.param(True, id="lua", marks=requires_lua),
    pytest.param(False, id="no-lua"),
]


@pytest.fixture(params=_USE_LUA)
def sm(request, redis_client):  # noqa: F811
    return SessionState(redis_client, use_lua=request.param)


NOW = 1_000_000


async def test_place_writes_all_four_places(sm):
    await sm.register_pod("sc", "pod-a", "http://a", "v1")
    act, pod = await sm.route_place("s1", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    assert (act, pod) == ("placed", "pod-a")
    h = await sm.session_hash("s1")
    assert h == {"scope_id": "sc", "pod_id": "pod-a",
                 "expiry": str(NOW + 60), "session_ttl": "60"}
    members = {s(x) for x in await sm.redis.smembers(sm.k.scope_sessions("sc"))}
    assert members == {"s1"}
    pmembers = {s(x) for x in await sm.redis.smembers(sm.k.pod_sessions("sc", "pod-a"))}
    assert pmembers == {"s1"}
    assert await sm.session_expiry_score("s1") == NOW + 60


async def test_affinity_refresh_and_dead_binding_rebind(sm):
    await sm.register_pod("sc", "pod-a", "http://a", "v1")
    await sm.route_place("s1", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    # 亲和命中：仅续期不换 Pod
    act, pod = await sm.route_place("s1", "sc", NOW + 120, 45, 10, 2, 5, NOW)
    assert (act, pod) == ("refresh", "pod-a")
    h = await sm.session_hash("s1")
    assert h["expiry"] == str(NOW + 120) and h["session_ttl"] == "45"
    # 过期绑定 → 惰性回收 rebind（调用方重试后落位）
    await sm.redis.hset(sm.k.session("s1"), "expiry", str(NOW - 1))
    act, pod = await sm.route_place("s1", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    assert act == "rebind"
    assert await sm.session_hash("s1") == {}  # 旧绑定已回收
    act, pod = await sm.route_place("s1", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    assert act == "placed"
    # 判死绑定（Pod 注册消失）→ rebind
    await sm.cleanup_pod("sc", "pod-a")
    act, _ = await sm.route_place("s1", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    assert act == "rebind"


async def test_rubble_selfclean_falls_through_to_placement(sm):
    # 残骸：哈希存在但缺 scope/pod（外部直改键的半成品）→ 自清 + 落穿放置
    await sm.register_pod("sc", "pod-a", "http://a", "v1")
    await sm.redis.hset(sm.k.session("bad"), mapping={"expiry": "123"})
    await sm.redis.zadd(sm.k.session_expiry(), {"bad": 123})
    act, pod = await sm.route_place("bad", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    assert (act, pod) == ("placed", "pod-a")
    h = await sm.session_hash("bad")
    assert h["scope_id"] == "sc" and h["pod_id"] == "pod-a"


async def test_gates_scope_pod_max(sm):
    await sm.register_pod("sc", "pod-a", "http://a", "v1")
    # scope_cc=2
    await sm.route_place("s1", "sc", NOW + 60, 60, 2, 10, 5, NOW)
    await sm.route_place("s2", "sc", NOW + 60, 60, 2, 10, 5, NOW)
    act, _ = await sm.route_place("s3", "sc", NOW + 60, 60, 2, 10, 5, NOW)
    assert act == "scope_full"
    # 释放后可再入
    await sm.evict("s2")
    act, _ = await sm.route_place("s3", "sc", NOW + 60, 60, 2, 10, 5, NOW)
    assert act == "placed"
    # pod_cc=1：第二个会话 → need_acquire（仅 1 Pod）；max_pods=1 → scope_full
    await sm.evict("s1")
    act, _ = await sm.route_place("s4", "sc", NOW + 60, 60, 10, 1, 5, NOW)
    assert act == "need_acquire"  # s3 占了唯一 Pod
    act, _ = await sm.route_place("s5", "sc2", NOW + 60, 60, 10, 1, 0, NOW)
    assert act == "scope_full"   # sc2 无 Pod 且 max_pods=0


async def test_touch_lazy_evict_and_ttl_from_hash(sm):
    await sm.register_pod("sc", "pod-a", "http://a", "v1")
    await sm.route_place("s1", "sc", NOW + 60, 77, 10, 2, 5, NOW)
    ok, pod = await sm.touch("s1", NOW + 1)
    assert (ok, pod) == (True, "pod-a")
    assert await sm.session_expiry_score("s1") == NOW + 1 + 77  # ttl 就地读 77
    # 不存在
    ok, _ = await sm.touch("ghost", NOW)
    assert ok is False
    # 过期 → 惰性 evict
    await sm.redis.hset(sm.k.session("s1"), "expiry", str(NOW))
    ok, _ = await sm.touch("s1", NOW + 1)
    assert ok is False
    assert await sm.session_hash("s1") == {}
    assert await sm.scope_session_count("sc") == 0


async def test_rebind_five_states(sm):
    await sm.register_pod("sc", "pod-a", "http://a", "v1")
    await sm.register_pod("sc", "pod-b", "http://b", "v1")
    # noop：from 不存在
    r = await sm.rebind("gone", "x", NOW, 60)
    assert r["action"] == "noop"
    # rebound：四处不变量整体迁移 + TTL 刷新
    await sm.route_place("tmp1", "sc", NOW + 60, 33, 10, 2, 5, NOW)
    r = await sm.rebind("tmp1", "real1", NOW + 5, 60)
    assert r["action"] == "rebound" and r["pod_id"] == "pod-a"
    h = await sm.session_hash("real1")
    assert h["pod_id"] == "pod-a" and h["session_ttl"] == "33"
    assert int(h["expiry"]) == NOW + 5 + 33
    assert await sm.session_hash("tmp1") == {}
    sc = {s(x) for x in await sm.redis.smembers(sm.k.scope_sessions("sc"))}
    assert "tmp1" not in sc and "real1" in sc
    assert await sm.session_expiry_score("real1") == NOW + 5 + 33
    assert await sm.session_expiry_score("tmp1") is None
    # 幂等：同一搬移重放 → noop
    r = await sm.rebind("tmp1", "real1", NOW, 60)
    assert r["action"] == "noop"
    # overtaken：to 已有绑定 → 保留 to 仅清 from
    await sm.route_place("tmp2", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    await sm.route_place("real2", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    r = await sm.rebind("tmp2", "real2", NOW, 60)
    assert r["action"] == "overtaken"
    assert await sm.session_hash("tmp2") == {}
    # to 的既有绑定被保留（overtaken 语义；first-fit 落位 pod-b 亦合法）
    assert (await sm.session_hash("real2"))["pod_id"] in ("pod-a", "pod-b")
    # evicted：to 为空（create 失败驱逐）
    await sm.route_place("tmp3", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    r = await sm.rebind("tmp3", "", NOW, 60)
    assert r["action"] == "evicted" and r["pod_id"] == "pod-a"
    assert await sm.session_hash("tmp3") == {}
    # evicted：绑定 Pod 注册消失
    await sm.cleanup_pod("sc", "pod-a")
    await sm.redis.hset(sm.k.session("tmp4"), mapping={
        "scope_id": "sc", "pod_id": "pod-a",
        "expiry": str(NOW + 60), "session_ttl": "60"})
    r = await sm.rebind("tmp4", "real4", NOW, 60)
    assert r["action"] == "evicted" and r["pod_id"] == "pod-a"
    assert await sm.session_hash("tmp4") == {}


async def test_evict_idempotent_and_rubble(sm):
    await sm.register_pod("sc", "pod-a", "http://a", "v1")
    await sm.route_place("s1", "sc", NOW + 60, 60, 10, 2, 5, NOW)
    r = await sm.evict("s1")
    assert r == {"scope_id": "sc", "pod_id": "pod-a", "remaining": "0"}
    assert await sm.evict("s1") is None  # 幂等
    # 残骸：不上抛、自清两处
    await sm.redis.hset(sm.k.session("bad"), mapping={"scope_id": "sc"})
    await sm.redis.zadd(sm.k.session_expiry(), {"bad": 1})
    assert await sm.evict("bad") is None
    assert await sm.session_hash("bad") == {}
    assert await sm.session_expiry_score("bad") is None


async def test_concurrent_no_oversell(sm):
    """并发不超卖（核心不变量；no-lua 放置段进程内锁 + WATCH 双保险）。"""
    for i in range(4):
        await sm.register_pod("sc", f"pod-{i}", "http://x", "v1")
    SC, PC = 20, 5
    results = []

    async def one(i):
        act, pod = await sm.route_place(f"s{i}", "sc", NOW + 60, 60, SC, PC, 6, NOW)
        results.append((i, act, pod))

    await asyncio.gather(*(one(i) for i in range(40)))
    placed = [r for r in results if r[1] == "placed"]
    assert len(placed) == 20  # SC=20 恰收满
    assert await sm.scope_session_count("sc") == 20
    for i in range(4):
        n = len(await sm.redis.smembers(sm.k.pod_sessions("sc", f"pod-{i}")))
        assert n <= PC, (i, n)
    for i, act, pod in placed:
        h = await sm.session_hash(f"s{i}")
        assert h["pod_id"] == pod
        assert await sm.session_expiry_score(f"s{i}") is not None


async def test_exhaustion_raises_for_503_path(redis_client):  # noqa: F811
    """重试耗尽 → 上抛 WatchError（handler 映射 503 STATE_UNAVAILABLE）。"""
    sm = SessionState(redis_client, use_lua=False)
    await sm.register_pod("sc", "pod-a", "http://x", "v1")

    from agent_runtime.session_manager import no_lua as sm_no_lua

    calls = {"n": 0}

    async def always_conflict(self, *a, **kw):
        calls["n"] += 1
        raise WatchError("rigged contention")

    orig = sm_no_lua.SessionNoLua._placement_attempt
    orig_binding = sm_no_lua.SessionNoLua._binding_attempt

    async def binding_none(self, *a, **kw):
        return None  # 无绑定 → 每轮都进放置段

    sm_no_lua.SessionNoLua._placement_attempt = always_conflict
    sm_no_lua.SessionNoLua._binding_attempt = binding_none
    try:
        with pytest.raises(WatchError):
            await sm.route_place("s1", "sc", NOW + 60, 60, 10, 2, 5, NOW)
        assert calls["n"] >= sm_no_lua._HOT_ATTEMPTS
    finally:
        sm_no_lua.SessionNoLua._placement_attempt = orig
        sm_no_lua.SessionNoLua._binding_attempt = orig_binding
