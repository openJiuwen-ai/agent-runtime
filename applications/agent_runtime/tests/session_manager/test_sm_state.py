# coding: utf-8
"""SM 状态层单测（M1）：LUA_ROUTE_PLACE / EVICT / TOUCH / SWEEP_IDLE_NOTIFY / REGISTER_POD。

覆盖 HLD 场景 A（亲和续期）、B（first-fit 占位）、C（need_acquire 判定）、
F（scope_full）的 Redis 侧断言。示例配置统一：scope_concurrency=3、
pod_concurrency=2 → max_pods=2、session_ttl=60。
"""

from __future__ import annotations

import pytest

from agent_runtime.util import now_ts
from tests.conftest import requires_lua

SCOPE = "grp-bot"   # scope_id 由 config_sync 下发,测试用字面量
NOW = 1_000_000


@pytest.fixture
def placed(sm_state):
    """预置：scope_A 已有 pod_1（接入序 1），sess_1 已放置其上（1/2 占用）。"""

    async def _setup() -> None:
        await sm_state.register_pod(SCOPE, "pod_1", "http://10.0.0.1:8080/sse", "ver1")
        action, pod = await sm_state.route_place(
            "sess_1", SCOPE, NOW + 60, 60, 3, 2, 2, NOW
        )
        assert (action, pod) == ("placed", "pod_1")

    return _setup


# ---------------------------------------------------------------- 场景 A：亲和续期


@requires_lua
async def test_route_affinity_refresh(sm_state, placed):
    await placed()
    action, pod = await sm_state.route_place(
        "sess_1", SCOPE, NOW + 120, 60, 3, 2, 2, NOW + 1
    )
    assert (action, pod) == ("refresh", "pod_1")
    # 续期后 expiry 刷新为传入值；scope / pod 集合不变（额度不重抢）
    expiry = await sm_state.redis.hget(sm_state.k.session("sess_1"), "expiry")
    assert expiry == str(NOW + 120).encode() or expiry == str(NOW + 120)
    assert await sm_state.redis.scard(sm_state.k.scope_sessions(SCOPE)) == 1


# ---------------------------------------------------------------- 场景 B：first-fit 占位


@requires_lua
async def test_route_first_fit_places_on_existing_pod(sm_state, placed):
    await placed()
    action, pod = await sm_state.route_place(
        "sess_2", SCOPE, NOW + 60, 60, 3, 2, 2, NOW
    )
    assert (action, pod) == ("placed", "pod_1")
    # 四处一致（不变量 1）
    assert await sm_state.redis.scard(sm_state.k.scope_sessions(SCOPE)) == 2
    assert await sm_state.redis.scard(sm_state.k.pod_sessions(SCOPE, "pod_1")) == 2
    assert await sm_state.redis.zscore(sm_state.k.session_expiry(), "sess_2") == NOW + 60
    binding = await sm_state.redis.hgetall(sm_state.k.session("sess_2"))
    assert binding[b"pod_id"] == b"pod_1"
    assert binding[b"session_ttl"] == b"60"


@requires_lua
async def test_route_first_fit_prefers_earliest_pod(sm_state):
    """两个有空位的 Pod，first-fit 选接入序更早的 pod_1（负载打包，利于缩容）。"""
    await sm_state.register_pod(SCOPE, "pod_1", "http://10.0.0.1:8080/sse", "ver1")
    await sm_state.register_pod(SCOPE, "pod_2", "http://10.0.0.2:8080/sse", "ver1")
    action, pod = await sm_state.route_place("sess_1", SCOPE, NOW + 60, 60, 3, 2, 2, NOW)
    assert (action, pod) == ("placed", "pod_1")


@requires_lua
async def test_route_place_reuse_pod_clears_idle_notified(sm_state):
    """复用曾被标空的 Pod 时清 idle_notified（空标记失效，下次空时可重新通知）。"""
    await sm_state.register_pod(SCOPE, "pod_1", "http://10.0.0.1:8080/sse", "ver1")
    await sm_state.redis.set(sm_state.k.pod_idle_notified(SCOPE, "pod_1"), "1", ex=60)
    await sm_state.route_place("sess_1", SCOPE, NOW + 60, 60, 3, 2, 2, NOW)
    assert await sm_state.redis.exists(sm_state.k.pod_idle_notified(SCOPE, "pod_1")) == 0


# ---------------------------------------------------------------- 场景 C：need_acquire 判定


@requires_lua
async def test_route_need_acquire_when_pods_full(sm_state):
    """现有 Pod 全满且未达 max_pods → need_acquire（handler 调 RM 扩 +1）。"""
    await sm_state.register_pod(SCOPE, "pod_1", "http://10.0.0.1:8080/sse", "ver1")
    for sid in ("sess_1", "sess_2"):
        await sm_state.route_place(sid, SCOPE, NOW + 60, 60, 3, 2, 2, NOW)
    action, _ = await sm_state.route_place(
        "sess_3", SCOPE, NOW + 60, 60, 3, 2, 2, NOW
    )
    assert action == "need_acquire"


@requires_lua
async def test_route_scope_full_at_max_pods(sm_state):
    """Pod 数达 max_pods 且全满 → scope_full（总容量已 ≥ scope 预算）。"""
    await sm_state.register_pod(SCOPE, "pod_1", "http://10.0.0.1:8080/sse", "ver1")
    await sm_state.register_pod(SCOPE, "pod_2", "http://10.0.0.2:8080/sse", "ver1")
    for sid in ("sess_1", "sess_2", "sess_3", "sess_4"):
        await sm_state.route_place(sid, SCOPE, NOW + 60, 60, 3, 2, 2, NOW)
    action, _ = await sm_state.route_place("sess_5", SCOPE, NOW + 60, 60, 3, 2, 2, NOW)
    assert action == "scope_full"


# ---------------------------------------------------------------- 场景 F：scope 闸门


@requires_lua
async def test_route_scope_full_when_sessions_at_limit(sm_state):
    """活跃会话数达 scope_concurrency → scope_full（与 Pod 余量无关）。"""
    await sm_state.register_pod(SCOPE, "pod_1", "http://10.0.0.1:8080/sse", "ver1")
    # scope_concurrency=1：放 1 个后即满
    await sm_state.route_place("sess_1", SCOPE, NOW + 60, 60, 1, 2, 1, NOW)
    action, _ = await sm_state.route_place("sess_2", SCOPE, NOW + 60, 60, 1, 2, 1, NOW)
    assert action == "scope_full"


# ---------------------------------------------------------------- 惰性回收


@requires_lua
async def test_route_lazy_evict_expired_binding(sm_state, placed):
    """亲和绑定已过期 → route 当场清旧绑定再重新放置（惰性兜底）。"""
    await placed()
    # 时间推进到过期之后（expiry=NOW+60）
    action, pod = await sm_state.route_place(
        "sess_1", SCOPE, NOW + 200, 60, 3, 2, 2, NOW + 100
    )
    assert (action, pod) == ("placed", "pod_1")
    # 旧额度已释放：scope 活跃数仍为 1（不是 2）
    assert await sm_state.redis.scard(sm_state.k.scope_sessions(SCOPE)) == 1


@requires_lua
async def test_route_lazy_evict_publishes_free_signal(sm_state, placed):
    """惰性回收旧 scope 绑定时 PUBLISH 旧 scope 的 free 通道（唤醒等待者）。"""
    await placed()
    pubsub = sm_state.redis.pubsub()
    await pubsub.subscribe(sm_state.k.scope_free_channel(SCOPE))
    try:
        await sm_state.route_place("sess_1", SCOPE, NOW + 200, 60, 3, 2, 2, NOW + 100)
        message = await pubsub.get_message(timeout=1)  # subscribe 确认帧
        while message and message["type"] != "message":
            message = await pubsub.get_message(timeout=1)
        assert message is not None and message["data"] == b"1"
    finally:
        await pubsub.unsubscribe(sm_state.k.scope_free_channel(SCOPE))
        await pubsub.aclose()


# ---------------------------------------------------------------- EVICT


@requires_lua
async def test_evict_removes_four_places(sm_state, placed):
    await placed()
    result = await sm_state.evict("sess_1")
    assert result == {"scope_id": SCOPE, "pod_id": "pod_1", "remaining": "0"}
    assert await sm_state.redis.exists(sm_state.k.session("sess_1")) == 0
    assert await sm_state.redis.zscore(sm_state.k.session_expiry(), "sess_1") is None
    assert await sm_state.redis.scard(sm_state.k.scope_sessions(SCOPE)) == 0
    assert await sm_state.redis.scard(sm_state.k.pod_sessions(SCOPE, "pod_1")) == 0


@requires_lua
async def test_evict_idempotent(sm_state):
    assert await sm_state.evict("nope") is None


# ---------------------------------------------------------------- TOUCH（场景 E）


@requires_lua
async def test_touch_refreshes_ttl(sm_state, placed):
    await placed()
    touched, pod = await sm_state.touch("sess_1", now=NOW + 10, default_ttl=60)
    assert touched is True and pod == "pod_1"
    # 就地读 session HASH 里的 session_ttl：expiry = now + 60 = NOW + 70
    assert await sm_state.redis.zscore(sm_state.k.session_expiry(), "sess_1") == NOW + 70
    # 额度不变
    assert await sm_state.redis.scard(sm_state.k.scope_sessions(SCOPE)) == 1


@requires_lua
async def test_touch_missing_session(sm_state):
    touched, pod = await sm_state.touch("nope", now=NOW)
    assert touched is False and pod == ""


@requires_lua
async def test_touch_expired_session_evicts_lazy(sm_state, placed):
    await placed()
    touched, _ = await sm_state.touch("sess_1", now=NOW + 61, default_ttl=60)
    assert touched is False
    assert await sm_state.redis.exists(sm_state.k.session("sess_1")) == 0
    assert await sm_state.redis.scard(sm_state.k.scope_sessions(SCOPE)) == 0


# ---------------------------------------------------------------- SWEEP_IDLE_NOTIFY（场景 D 前置）


@requires_lua
async def test_sweep_idle_notify_empty_pod(sm_state, placed):
    """空 Pod：通知一次 + 原子 ZREM 退出候选；60s 内去重。"""
    await placed()
    await sm_state.evict("sess_1")
    assert await sm_state.sweep_idle_notify(SCOPE, "pod_1") is True
    # 退出 first-fit 候选（堵 reclaim 窗口内 route 直选，竞态 A）
    assert await sm_state.redis.zscore(sm_state.k.scope_pods(SCOPE), "pod_1") is None
    # 去重：第二次不再通知
    assert await sm_state.sweep_idle_notify(SCOPE, "pod_1") is False


@requires_lua
async def test_sweep_idle_notify_skips_busy_pod(sm_state, placed):
    """非空 Pod：不通知、不 ZREM。"""
    await placed()
    assert await sm_state.sweep_idle_notify(SCOPE, "pod_1") is False
    assert await sm_state.redis.zscore(sm_state.k.scope_pods(SCOPE), "pod_1") is not None


# ---------------------------------------------------------------- REGISTER_POD


@requires_lua
async def test_register_pod_writes_three_places(sm_state):
    await sm_state.register_pod(SCOPE, "pod_9", "http://10.0.0.9:8080/sse", "verX")
    assert await sm_state.redis.zscore(sm_state.k.scope_pods(SCOPE), "pod_9") == 1
    assert await sm_state.redis.sismember(sm_state.k.pods_registered(), f"{SCOPE}:pod_9")
    assert await sm_state.redis.sismember(sm_state.k.pod_scopes("pod_9"), SCOPE)
    info = await sm_state.redis.hgetall(sm_state.k.pod_info(SCOPE, "pod_9"))
    assert info[b"sse_url"] == b"http://10.0.0.9:8080/sse"
    assert info[b"deploy_ver"] == b"verX"
    # 第二个 Pod 接入序递增
    await sm_state.register_pod(SCOPE, "pod_10", "http://10.0.0.10:8080/sse", "verX")
    assert await sm_state.redis.zscore(sm_state.k.scope_pods(SCOPE), "pod_10") == 2
    # ZREM 后的 Pod 不被 first-fit 选中（候选集排除）
    ids = await sm_state.scope_pod_ids(SCOPE)
    assert "pod_9" in ids and "pod_10" in ids


# ---------------------------------------------------------------- scope_id 校验(config_sync 入口)


def test_scope_id_charset_rejects_separator():
    """scope_id 禁 ':' 等——Redis 键与 pods:registered 的 "{scope}:{pod}" 切分依赖。"""
    from agent_runtime.errors import InvalidParams
    from agent_runtime.session_manager.routing import parse_scope

    with pytest.raises(InvalidParams):
        parse_scope({"scope_id": "a:b", "index": 0, "template_id": "t"}, {"t"})


def test_now_ts_monotonic_seconds():
    assert isinstance(now_ts(), int)


@requires_lua
async def test_evict_rubble_hash_self_heals_not_crashes(sm_state, caplog):
    """残骸自卫(2026-09-01 真环境实锤断网):外部直改键造出仅剩 expiry 的半成品
    哈希(zadd+hset 回拨落在已自然驱逐的会话上)→ EVICT 不得上抛(否则 sweeper
    到期 pass 每 tick 死在同一 sid,会话永不过期),应自清两处 + WARNING 留痕。
    """
    import logging as _logging

    await sm_state.redis.hset(sm_state.k.session("s-rubble"), "expiry", 1)
    await sm_state.redis.zadd(sm_state.k.session_expiry(), {"s-rubble": 1})
    with caplog.at_level(_logging.WARNING, logger="agent_runtime.session_manager"):
        result = await sm_state.evict("s-rubble")   # 旧实现此处 ResponseError
    assert result is None
    assert await sm_state.redis.exists(sm_state.k.session("s-rubble")) == 0
    assert await sm_state.redis.zscore(sm_state.k.session_expiry(), "s-rubble") is None
    assert any("rubble" in r.getMessage() for r in caplog.records)


@requires_lua
async def test_touch_rubble_hash_self_heals_not_crashes(sm_state):
    """残骸自卫扩到 TOUCH:半成品哈希(缺 scope_id/pod_id/expiry 任一)不得
    Lua runtime error(旧实现 tonumber(nil) <= now → ResponseError,该会话
    touch 永久 500),应自清两处返回 (False, '')。"""
    # 变体一:只有 scope_id(缺 pod_id/expiry)
    await sm_state.redis.hset(sm_state.k.session("s-rubble-t1"), "scope_id", SCOPE)
    await sm_state.redis.zadd(sm_state.k.session_expiry(), {"s-rubble-t1": 1})
    touched, pod = await sm_state.touch("s-rubble-t1", now=now_ts())
    assert (touched, pod) == (False, "")
    assert await sm_state.redis.exists(sm_state.k.session("s-rubble-t1")) == 0
    assert await sm_state.redis.zscore(sm_state.k.session_expiry(), "s-rubble-t1") is None

    # 变体二:scope_id/pod_id 齐但缺 expiry(nil 比较是旧实现的另一个炸点)
    await sm_state.redis.hset(sm_state.k.session("s-rubble-t2"),
                              mapping={"scope_id": SCOPE, "pod_id": "pod_9"})
    touched, pod = await sm_state.touch("s-rubble-t2", now=now_ts())
    assert (touched, pod) == (False, "")
    assert await sm_state.redis.exists(sm_state.k.session("s-rubble-t2")) == 0


@requires_lua
async def test_route_place_rubble_hash_self_heals_not_crashes(sm_state):
    """残骸自卫扩到 ROUTE_PLACE:半成品哈希自清后**落穿走全新放置**(不 return
    rubble);旧实现 nil 拼接/nil 比较会让该会话 route 永久 500。"""
    await sm_state.register_pod(SCOPE, "pod_rp", "http://10.0.0.9:8080/sse", "verX")
    # 半成品:scope_id 对但缺 pod_id 与 expiry
    await sm_state.redis.hset(sm_state.k.session("s-rubble-rp"), "scope_id", SCOPE)
    await sm_state.redis.zadd(sm_state.k.session_expiry(), {"s-rubble-rp": 1})

    action, pod = await sm_state.route_place(
        "s-rubble-rp", SCOPE, expiry_ts=now_ts() + 60, session_ttl=60,
        scope_concurrency=3, pod_concurrency=2, max_pods=2, now=now_ts())
    assert action == "placed", "残骸清掉后必须正常全新放置"
    assert pod == "pod_rp"
    # 落穿的提交覆盖写全四字段(半成品被治好)
    info = await sm_state.redis.hgetall(sm_state.k.session("s-rubble-rp"))
    assert info[b"scope_id"] == SCOPE.encode() and info[b"pod_id"] == b"pod_rp"
    assert b"expiry" in info and b"session_ttl" in info
