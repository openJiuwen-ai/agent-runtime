# coding: utf-8
"""组件全链路测试：route / touch / sweeper / notify（场景 A/B/C/D/E/F/G）。

SM（orchestrator + config_store + sweeper）与 RM（orchestrator，FakeK8s）真实互调，
 fakeredis 共享状态，SQLite 存配置。示例配置：scope_concurrency=3、
pod_concurrency=2 → max_pods=2、session_ttl=60。
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from agent_runtime.config import SM_KEY_PREFIX
from agent_runtime.errors import (
    ErrorCode,
    HTTP_STATUS_MAP,
    NoPodAvailable,
    ScopeFull,
)
from agent_runtime.util import now_ts
from tests.conftest import requires_lua

SCOPE = "scope-main"   # 与 conftest.seed_template 播种的 scope_id 一致


# ---------------------------------------------------------------- 场景 B/C：route 全链路


@requires_lua
async def test_route_first_session_deploys_pod(runtime):
    """无亲和 + 无 Pod：触发 RM deploy（场景 C 的 RM 侧）→ 登记候选 → 占额度。"""
    await runtime.seed_template()
    result = await runtime.route("sess_1")
    assert result["pod_id"].startswith("agentserver-")
    assert result["pod_sse_url"].startswith("http://10.42.")
    # SM 侧候选集 + 注册三处
    pods = await runtime.sm_state.scope_pod_ids(SCOPE)
    assert pods == [result["pod_id"]]
    assert await runtime.sm_state.redis.scard(runtime.sm_state.k.pods_registered()) == 1
    # RM 侧池
    assert await runtime.rm_state.pod_count(SCOPE) == 1


@requires_lua
async def test_route_affinity_sticks_to_pod(runtime):
    """场景 A：同 session 再 route 返回原 Pod（零冷启动，不重抢额度）。"""
    await runtime.seed_template()
    first = await runtime.route("sess_1")
    second = await runtime.route("sess_1")
    assert second["pod_id"] == first["pod_id"]
    assert await runtime.sm_state.redis.scard(runtime.sm_state.k.scope_sessions(SCOPE)) == 1


@requires_lua
async def test_route_packs_first_fit_then_scales_out(runtime):
    """场景 B+C：sess_1/sess_2 打包 pod_1（1/2→2/2），sess_3 触发扩 pod_2。"""
    await runtime.seed_template()
    r1 = await runtime.route("sess_1")
    r2 = await runtime.route("sess_2")
    r3 = await runtime.route("sess_3")
    assert r2["pod_id"] == r1["pod_id"]          # first-fit 打包
    assert r3["pod_id"] != r1["pod_id"]          # 满 → 扩 +1
    assert len(await runtime.sm_state.scope_pod_ids(SCOPE)) == 2


@requires_lua
async def test_route_scope_full_at_max_pods_fast_fail(runtime):
    """场景 F（2026-09 快失败）：Pod 全满 + 达 max_pods → scope_full →
    立即 503 ScopeFull（带 retry_after），不等待、不留 waiters 键。"""
    await runtime.seed_template(scope_concurrency=4, pod_concurrency=2)  # max_pods=2
    await runtime.route("sess_1")
    await runtime.route("sess_2")   # pod_1 满
    await runtime.route("sess_3")   # 扩 pod_2
    await runtime.route("sess_4")   # pod_2 满（2/2）
    # 第 5 个：Pod 全满 + 达 max_pods → scope_full → 立即 503（无等待）
    t0 = time.monotonic()
    with pytest.raises(ScopeFull) as exc_info:
        await runtime.route("sess_5")
    assert time.monotonic() - t0 < 1.0          # 快失败：Lua 闸门毫秒级返回
    assert exc_info.value.retry_after == 1
    assert exc_info.value.code == ErrorCode.SCOPE_FULL
    assert HTTP_STATUS_MAP[ErrorCode.SCOPE_FULL] == 503   # 错误码契约
    # 拆除净空：不创建等待队列键
    assert await runtime.sm_state.redis.keys(
        f"{SM_KEY_PREFIX}:scope:{SCOPE}:waiters"
    ) == []


# ---------------------------------------------------------------- 场景 E：touch


@requires_lua
async def test_touch_refresh_and_missing(runtime, caplog):
    await runtime.seed_template()
    await runtime.route("sess_1")
    with caplog.at_level(logging.INFO, logger="agent_runtime.session_manager"):
        assert await runtime.orchestrator.touch("sess_1") is True
        assert await runtime.orchestrator.touch("nope") is False
    # 未命中必须 INFO 留痕（生产排障入口）；命中不得刷 INFO（保活高频）
    missed = [r for r in caplog.records if "touch missed" in r.getMessage()]
    assert len(missed) == 1 and "nope" in missed[0].getMessage()


# ---------------------------------------------------------------- 场景 D：老化回收链


@requires_lua
async def test_sweeper_expires_session_and_notifies_idle(runtime, monkeypatch):
    """到期 pass：evict 过期会话 → 空 Pod pass：idle_consider → RM 转 idle（场景 D）。"""
    await runtime.seed_template()
    result = await runtime.route("sess_1")
    pod_id = result["pod_id"]

    # 时间推进 61s：把到期时间改到过去（不真睡）
    await runtime.sm_state.redis.zadd(
        runtime.sm_state.k.session_expiry(), {pod_id and "sess_1": now_ts() - 1}
    )
    await runtime.sm_state.redis.hset(
        runtime.sm_state.k.session("sess_1"), "expiry", now_ts() - 1
    )

    notified = []
    original_consider = runtime.rm_facade.idle_consider

    async def _spy(pod_id, scope_id):
        notified.append((pod_id, scope_id))
        return await original_consider(pod_id=pod_id, scope_id=scope_id)

    runtime.sm_sweeper.rm.idle_consider = _spy
    await runtime.sm_sweeper.sweep_once()
    await asyncio.sleep(0.1)   # idle_consider 是 fire-and-forget，让出事件循环

    assert notified == [(pod_id, SCOPE)]
    # SM：候选集 ZREM；RM：入 idle 暖池
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []
    assert pod_id in await runtime.rm_state.idle_pods(SCOPE)
    # 注册中间态：pods:registered 仍持有（待 RM 回收后 notify 清理，不变量 5）
    assert await runtime.sm_state.redis.sismember(
        runtime.sm_state.k.pods_registered(), f"{SCOPE}:{pod_id}"
    )


@requires_lua
async def test_sweeper_reclaim_after_pod_ttl(runtime):
    """场景 K：idle 超 pod_ttl → reclaim：K8s delete + PURGE + notify_pod_dead 清 SM。"""
    await runtime.seed_template(pod_ttl=100)
    result = await runtime.route("sess_1")
    pod_id = result["pod_id"]

    # 手工走 D 链尾：过期 + sweep（SM ZREM + idle_consider）
    await runtime.sm_state.redis.hset(
        runtime.sm_state.k.session("sess_1"), "expiry", now_ts() - 1
    )
    await runtime.sm_state.redis.zadd(
        runtime.sm_state.k.session_expiry(), {"sess_1": now_ts() - 1}
    )
    await runtime.sm_sweeper.sweep_once()
    await asyncio.sleep(0.1)   # idle_consider 是 fire-and-forget
    assert pod_id in await runtime.rm_state.idle_pods(SCOPE)

    # idle_since 前移 101s（pod_ttl=100），跑 reclaim
    await runtime.rm_state.redis.set(
        runtime.rm_state.k.pod_idle_since(pod_id), now_ts() - 101
    )
    await runtime.rm_sweeper.reclaim_once()

    assert pod_id in runtime.k8s.deleted                    # K8s 已删
    assert pod_id not in await runtime.rm_state.all_pod_ids()   # RM 已 PURGE
    assert await runtime.sm_state.registered_pods() == []   # SM 注册已清（notify）
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.pod_info(SCOPE, pod_id)
    ) == 0


# ---------------------------------------------------------------- 场景 G：notify_pod_dead


@requires_lua
async def test_notify_pod_dead_cleans_sessions_and_registrations(runtime):
    await runtime.seed_template()
    result = await runtime.route("sess_1")
    pod_id = result["pod_id"]

    invalidated = await runtime.sm_facade.notify_pod_dead(pod_id)
    assert invalidated == {"invalidated": ["sess_1"]}
    # 会话四处全清；注册三处全清
    assert await runtime.sm_state.redis.exists(runtime.sm_state.k.session("sess_1")) == 0
    assert await runtime.sm_state.registered_pods() == []
    assert await runtime.sm_state.pod_scopes(pod_id) == []
    # 幂等
    assert await runtime.sm_facade.notify_pod_dead(pod_id) == {"invalidated": []}


# ---------------------------------------------------------------- 场景 I：acquire 复用暖 Pod


@requires_lua
async def test_acquire_reuses_idle_warm_pod(runtime):
    """暖 Pod（场景 H 产生或刚腾空）被下一个 acquire 零部署复用。"""
    await runtime.seed_template(min_idle_pods=1)
    # 先 route 一次（首 acquire 建 scope:config + 部署 pod_1），再腾空转 idle
    first = await runtime.route("sess_1")
    await runtime.sm_state.evict("sess_1")
    assert await runtime.sm_state.sweep_idle_notify(SCOPE, first["pod_id"]) is True
    await runtime.rm_facade.idle_consider(pod_id=first["pod_id"], scope_id=SCOPE)

    result = await runtime.route("sess_2")
    assert result["pod_id"] == first["pod_id"]          # 复用暖 Pod，零部署
    assert await runtime.rm_state.idle_pods(SCOPE) == []


@requires_lua
async def test_autoscale_prewarms_to_min_idle(runtime):
    """场景 H：min_idle_pods=1，Pod 在用（idle=0）→ autoscale 预建热备。"""
    await runtime.seed_template(min_idle_pods=1)
    await runtime.route("sess_1")                        # 建 scope:config + pod 在用
    assert await runtime.rm_state.idle_pods(SCOPE) == []

    await runtime.rm_sweeper.autoscale_once()
    idle = await runtime.rm_state.idle_pods(SCOPE)
    assert len(idle) == 1                                # 热备 1 个（min_idle 达标）
    # 热备 Pod 在 K8s 已存在
    pod = await runtime.k8s.get_pod(idle[0], "default")
    assert pod is not None and pod.ready


@requires_lua
async def test_acquire_deploy_failure_raises_no_pod(runtime):
    """deploy 失败 → DeployFailed → SM 映射 NO_POD_AVAILABLE；占位已清可重试。"""
    await runtime.seed_template()
    runtime.k8s.deploy_failures = 1
    with pytest.raises(NoPodAvailable):
        await runtime.route("sess_1")
    # 红线：错误路径占位已清
    assert await runtime.rm_state.deploying_count(SCOPE) == 0
    # 重试成功
    result = await runtime.route("sess_1", request_id="req-retry")
    assert result["pod_id"]


@requires_lua
async def test_route_idempotent_replay_via_handler_idempotency(runtime):
    """request_id 重试不重复抢额度/扩 Pod（handler 层幂等缓存）。"""
    from openjiuwen_runtime.service.envelope import Metadata
    from agent_runtime.session_manager.handlers import handle_route

    await runtime.seed_template()

    class _Ctx:
        class sysctx:  # noqa: N801 - handler 经 sysctx 取服务
            sm_orchestrator = runtime.orchestrator
            sm_config_store = runtime.config_store
            rm_facade = runtime.rm_facade

        def __init__(self, request_id):
            self._idem_request_id = request_id
            from openjiuwen_runtime.service.context.primitives.idempotency import Idempotency

            self.idempotency = Idempotency(
                runtime.redis, prefix=f"{SM_KEY_PREFIX}:idem"
            )

        @property
        def request_id(self):
            return self._idem_request_id

    class _Env:
        type = "route"
        rawdata = {}

        def __init__(self, request_id):
            self.metadata = Metadata(request_id=request_id, session_id="sess_1",
                                     user_id="user", bot_id="bot",
                                     extra={"group_id": "grp"})

    ctx = _Ctx("req-idem-1")
    env = _Env("req-idem-1")
    first = await handle_route(ctx, env)
    second = await handle_route(ctx, env)      # 重试 → 幂等回放（ResponseEnvelope）
    assert second.rawdata["pod_id"] == first["pod_id"]
    assert await runtime.sm_state.redis.scard(
        runtime.sm_state.k.scope_sessions(SCOPE)
    ) == 1                                    # 只占一份额度
