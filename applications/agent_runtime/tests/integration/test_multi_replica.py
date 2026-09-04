# coding: utf-8
"""多副本（双实例）专项用例：跨副本语义的确定性验证（离线，fakeredis）。

进程内跑两个完整 App（各自 SystemContext + 全部后台 Job），共享同一
FakeRedis / SQLite / FakeK8s —— 等价两个副本指向同一 Redis/DB/K8s。

覆盖（对应 HLD「多副本无状态 + tick 级选主」承诺）：
- 身份与共享态：instance_id 互异、A 写 B 读；
- 准入闸门跨副本全局生效：并发突发不超收（ROUTE_PLACE 原子仲裁，2026-09
  起超收面为立即 503 SCOPE_FULL 快失败）；
- deploy 锁跨副本竞争：部署窗口零重叠、占位清干净、输家复用暖 Pod；
- 幂等跨副本重放：同 request_id 落不同副本返回同结果；
- 配置失效传播：B 改配置 → A 缓存即失效；
- 选主互斥：每 (job, epoch) 恒一 winner、双实例均参选；
- sweeper tick 互斥与并发收敛。

注意（与单进程直觉的关键差异）：跨副本冷竞争时，deploy 锁输家清占位后
重跑 ACQUIRE 只见赢家 in-use Pod → 自己再部署一个（max_pods 内），空 Pod
经 empty-pod pass → idle_consider → reclaim 自愈。因此部署竞争类用例断言
「窗口零重叠 + Pod ≤ max_pods + 占位清空」，不断言「恰好 1 个 Pod」。
"""

from __future__ import annotations

import asyncio
import json

from agent_runtime.config import RM_KEY_PREFIX, SM_KEY_PREFIX
from tests.conftest import requires_lua
from tests.integration._dual_harness import scope_of

# 键前缀取自 config 常量（带 hash tag），断言不硬编码字面键名
SM = f"{SM_KEY_PREFIX}:"
RM = f"{RM_KEY_PREFIX}:"

# ---------------------------------------------------------------- 基础


@requires_lua
async def test_two_instances_distinct_ids_shared_state(dual):
    """双实例 instance_id 互异（RM 镜像 SM），A 路由的会话 B 可 touch。"""
    a, b = dual.sysctx(0), dual.sysctx(1)
    assert a.instance_id != b.instance_id
    assert a.rm_sysctx.instance_id == a.instance_id
    assert b.rm_sysctx.instance_id == b.instance_id

    await dual.seed_template()
    status, raw, body = await dual.post(0, "route", session_id="s1")
    assert status == 200, body
    assert raw["pod_id"].startswith("agentserver-")

    status, raw, body = await dual.post(1, "touch", session_id="s1")
    assert status == 200, body
    assert raw == {"touched": True}


@requires_lua
async def test_route_alternating_replicas_affinity_single_session(dual):
    """同 session 交替打到 A/B/A/B：亲和命中同一 Pod，scope 仅一会话。"""
    await dual.seed_template()
    pods = set()
    for i in (0, 1, 0, 1):
        status, raw, body = await dual.post(i, "route", session_id="s1")
        assert status == 200, body
        pods.add(raw["pod_id"])
    assert len(pods) == 1

    scope = scope_of()
    assert await dual.redis.scard(
        f"{SM}scope:{scope}:sessions") == 1


@requires_lua
async def test_healthz_reports_instance(dual):
    """/healthz 就绪探针返回各自 instance_id（K8s 探针 / e2e 实例观测用）。"""
    for i, iid in ((0, "replica-a"), (1, "replica-b")):
        status, body = await dual.healthz(i)
        assert status == 200, body
        assert body["ok"] is True
        assert body["instance_id"] == iid


# ---------------------------------------------------------------- 准入闸门（跨副本全局）


@requires_lua
async def test_cross_replica_burst_no_over_admission(dual):
    """并发突发交替打 A/B：准入闸门（Lua 原子仲裁）全局生效，不因双副本超收。

    cc=2/pc=1（max_pods=2）：先串行放满 2 个会话，再 8 并发交替打 A/B——
    亲和续期的 2 个返回 200，其余全部撞 scope 闸门 → 立即 503 SCOPE_FULL
    快失败（2026-09 起场景 F 无等待队列），零等待、零额外 Redis 写。
    """
    await dual.seed_template(scope_concurrency=2, pod_concurrency=1)
    for sid in ("s1", "s2"):
        status, _, body = await dual.post(0, "route", session_id=sid)
        assert status == 200, body
    scope = scope_of()

    async def _attempt(sid):
        status, _, body = await dual.post(0 if sid in ("s1", "s2") else 1,
                                          "route", session_id=sid)
        return status, body.get("error_code")

    # 8 并发：s1/s2 亲和续期（200）+ 6 个新会话（撞闸门 → SCOPE_FULL）
    outcomes = await asyncio.gather(*[
        _attempt(sid) for sid in ("s1", "s2", *(f"burst-{i}" for i in range(6)))
    ])
    ok = [outcome for outcome in outcomes if outcome[0] == 200]
    rejected = [outcome for outcome in outcomes
                if outcome == (503, "SCOPE_FULL")]
    assert len(ok) == 2, outcomes               # 恰好亲和续期的 2 个
    assert len(rejected) == 6, outcomes         # 其余全部立即快失败
    assert len(ok) + len(rejected) == 8

    assert await dual.redis.scard(
        f"{SM}scope:{scope}:sessions") == 2
    # 拆除净空：无等待队列键、无部署占位
    assert await dual.redis.keys(
        f"{SM}scope:{scope}:waiters") == []
    assert await dual.redis.zcard(
        f"{RM}resource:scope:{scope}:deploying") == 0


# ---------------------------------------------------------------- deploy 锁（跨副本竞争）


@requires_lua
async def test_deploy_lock_serializes_cross_replica_deploys(dual):
    """并发冷启动打到 A/B：leader 部署、follower 等待复用——恰好 1 个 Pod。

    SlowFakeK8s deploy_delay=0.4s（deploy 全程持锁）。cc=4/pc=2 →
    follower 上限 pc-1=1：A/B 并发冷启动时输家进等待室，leader 的 Pod
    注册后直接复用（不再自建第 2 个空 Pod——M8 修复的跨副本冷竞争浪费）；
    pod1 满后 s3 才触发第 2 次部署。
    """
    dual.k8s.deploy_delay = 0.4
    await dual.seed_template(scope_concurrency=4, pod_concurrency=2)
    scope = scope_of()

    async def _route(i, sid):
        status, raw, body = await dual.post(i, "route", session_id=sid)
        assert status == 200, body
        return raw

    # A/B 并发冷启动竞争：1 个 leader + 1 个 follower，双方落同一 Pod
    first, second = await asyncio.gather(_route(0, "s1"), _route(1, "s2"))
    assert len(dual.k8s.deploy_log) == 1          # follower 复用，零额外部署
    assert second["pod_id"] == first["pod_id"]

    # pod1 已满（2/2）→ s3 才触发第 2 次部署
    await _route(0, "s3")
    assert len(dual.k8s.deploy_log) == 2
    assert not dual.k8s.deploy_windows_overlap()   # 两次部署窗口无交集
    assert await dual.redis.zcard(
        f"{RM}resource:scope:{scope}:deploying") == 0
    assert await dual.redis.zcard(
        f"{RM}resource:scope:{scope}:deploy_followers") == 0
    assert await dual.redis.scard(
        f"{SM}scope:{scope}:sessions") == 3


@requires_lua
async def test_deploy_follower_cap_strict_fast_fail(dual):
    """follower 等待室满（pc-1）→ overflow 严格快失败，不部署不等待。

    cc=8/pc=2 → max_pods=4（占位预算充足，4 个并发都能拿到占位），
    follower 上限 pc-1=1：1 leader + 1 follower 成功（pod1 2/2 满），
    第 3/4 个在闸门处 503 NO_POD_AVAILABLE 快失败。
    """
    dual.k8s.deploy_delay = 0.4
    await dual.seed_template(scope_concurrency=8, pod_concurrency=2)
    scope = scope_of()

    async def _attempt(i):
        status, _, body = await dual.post(i % 2, "route", session_id=f"cap-{i}")
        return status, body.get("error_code")

    outcomes = await asyncio.gather(*[_attempt(i) for i in range(4)])
    ok = [outcome for outcome in outcomes if outcome[0] == 200]
    fast_fail = [outcome for outcome in outcomes
                 if outcome == (503, "NO_POD_AVAILABLE")]
    assert len(ok) == 2, outcomes
    assert len(fast_fail) == 2, outcomes
    assert len(dual.k8s.deploy_log) == 1          # 恰好 1 次部署
    assert await dual.redis.zcard(
        f"{RM}resource:scope:{scope}:deploying") == 0
    assert await dual.redis.zcard(
        f"{RM}resource:scope:{scope}:deploy_followers") == 0
    assert await dual.redis.scard(
        f"{SM}scope:{scope}:sessions") == 2   # pod1 2/2 满


@requires_lua
async def test_follower_fails_when_leader_deploy_fails(dual):
    """leader 部署失败（锁释放且无进展）→ follower 不接管，直接失败。

    FakeK8s deploy_failures=1：leader 的 deploy 抛 DeployFailed（错误路径
    清占位）。follower 轮询见锁空闲无进展 → 同样 503；占位/等待室全清。
    """
    # deploy_failures 是内层 FakeK8s 的旋钮（经 set_deploy_failures 写透）；
    # 失败必须是「慢」的——瞬时失败时锁毫秒级释放，第二个请求会直接抢到
    # 已释放的锁部署成功，根本不进 follower 等待室
    dual.k8s.deploy_delay = 0.5
    dual.k8s.set_deploy_failures(1)
    await dual.seed_template(scope_concurrency=4, pod_concurrency=2)
    scope = scope_of()

    async def _attempt(i):
        status, _, body = await dual.post(i % 2, "route", session_id=f"fail-{i}")
        return status, body.get("error_code")

    outcomes = await asyncio.gather(*[_attempt(i) for i in range(2)])
    assert all(outcome == (503, "NO_POD_AVAILABLE")
               for outcome in outcomes), outcomes
    assert await dual.redis.zcard(
        f"{RM}resource:scope:{scope}:deploying") == 0
    assert await dual.redis.zcard(
        f"{RM}resource:scope:{scope}:deploy_followers") == 0


@requires_lua
async def test_deploy_loser_reuses_other_replicas_warm_pod(dual):
    """他副本持 deploy 锁并部署**暖 Pod**（idle）：本侧占位→抢锁失败→
    重试复用其暖 Pod，零部署、占位清干净（corner_cases:169 的 HTTP 版）。"""
    from agent_runtime.resource_manager.orchestrator import _deploy_ver
    from agent_runtime.resource_manager.state import ResourceState
    from agent_runtime.util import now_ts

    await dual.seed_template(scope_concurrency=3, pod_concurrency=2)
    scope = scope_of()
    rm_state = ResourceState(dual.redis)
    _, template = await dual.sysctx(0).sm_config_store.resolve("user", "grp", "bot")

    await dual.redis.set(rm_state.k.lock_deploy(scope), "other-replica",
                         nx=True, ex=30)

    async def _finish_other_deploy():
        await asyncio.sleep(0.6)
        await rm_state.register_pod(
            pod_id="pod-other", scope_id=scope,
            pod_sse_url="http://10.42.0.9:8080/sse", pod_ip="10.42.0.9",
            namespace="default",
            deploy_ver=_deploy_ver(template.deploy_subset()),
            deploy_token="other-token", idle_flag=True, now=now_ts())
        await dual.redis.delete(rm_state.k.lock_deploy(scope))

    task = asyncio.get_running_loop().create_task(_finish_other_deploy())
    status, raw, body = await dual.post(0, "route", session_id="s1")
    await task
    assert status == 200, body
    assert raw["pod_id"] == "pod-other"
    assert len(dual.k8s.deploy_log) == 0                    # 本侧零部署
    assert await dual.redis.zcard(
        f"{RM}resource:scope:{scope}:deploying") == 0


# ---------------------------------------------------------------- 幂等 / 配置传播


@requires_lua
async def test_idempotent_replay_across_replicas(dual):
    """同 request_id：A 首发、B 重放 → 响应一致、仅一会话（幂等态在 Redis）。"""
    await dual.seed_template()
    scope = scope_of()

    status, first, body = await dual.post(
        0, "route", session_id="s1", request_id="req-idem")
    assert status == 200, body
    status, second, body = await dual.post(
        1, "route", session_id="s1", request_id="req-idem")
    assert status == 200, body
    assert second["pod_id"] == first["pod_id"]
    assert second["pod_sse_url"] == first["pod_sse_url"]

    assert await dual.redis.scard(
        f"{SM}scope:{scope}:sessions") == 1


@requires_lua
async def test_config_sync_on_b_takes_effect_on_a(dual):
    """B 上 config_sync（B 类变更）→ 路由快照原子覆盖 → A 下次 route 见新值。

    快照是共享 Redis 单键（多副本同读），无失效传播环节;观察点用
    **新会话的路由**：touch 只刷新会话 HASH 里落位时存的 ttl，
    不重新 resolve（见 orchestrator.touch），不能用它观察配置传播。
    """
    await dual.seed_template(session_ttl=60)
    scope = scope_of()
    status, _, body = await dual.post(0, "route", session_id="s1")
    assert status == 200, body
    snapshot_key = f"{SM}routing:snapshot"
    ver_before = await dual.get(snapshot_key)

    from tests.conftest import split_sync_payload

    status, _, body = await dual.post(
        1, "config_sync",
        rawdata=split_sync_payload(
            [{"template_id": "tpl-1", "agent_image": "agentserver:1.0",
              "namespace": "default", "session_ttl": 90}],
            [{"scope_id": scope, "index": 0,
              "template_id": "tpl-1", "routing_rules": ""}]))
    assert status == 200, body
    assert await dual.redis.exists(snapshot_key) == 1
    assert await dual.get(snapshot_key) != ver_before   # 快照已覆盖

    from agent_runtime.util import now_ts

    status, raw, body = await dual.post(0, "route", session_id="s2")
    assert status == 200, body
    expiry = await dual.redis.zscore(f"{SM}session_expiry", "s2")
    assert now_ts() + 80 <= int(expiry) <= now_ts() + 95   # 用了新 ttl=90
    ttl = await dual.redis.hget(f"{SM}session:s2", "session_ttl")
    assert int(ttl) == 90


# ---------------------------------------------------------------- 选主互斥


@requires_lua
async def test_single_leader_one_winner_per_epoch(dual):
    """每 (job, epoch) 恒一 winner 且 ∈ candidates；双实例均参选。

    确定性断言：winner∈candidates、candidates 并集含双实例（两副本都在
    竞争）。winner 轮换（SRANDMEMBER 随机）只记录直方图不硬断言——5 个
    epoch 内单侧概率 ~6%，硬断言会引入抖动（见用例文档注释）。
    """
    from tests.integration._dual_harness import REPLICA_IDS

    sampled = await asyncio.gather(*[
        dual.sample_election(job, duration=5.5, interval=0.2)
        for job in ("sm_sweep", "rm_autoscale")])
    for job, samples in zip(("sm_sweep", "rm_autoscale"), sampled):
        epochs = {e: s for e, s in samples.items()
                  if "winner" in s and "candidates" in s}
        assert len(epochs) >= 3, f"{job} 采样 epoch 过少: {samples}"

        candidates_union: set[str] = set()
        winners: dict[str, int] = {}
        for epoch, s in epochs.items():
            assert s["winner"] in s["candidates"], (job, epoch, s)
            candidates_union.update(s["candidates"])
            winners[s["winner"]] = winners.get(s["winner"], 0) + 1

        assert candidates_union == set(REPLICA_IDS), (job, candidates_union)
        print(f"[election] {job}: winners={winners} epochs={len(epochs)}")


@requires_lua
async def test_sweeper_skips_when_other_replica_holds_tick(dual):
    """他副本持 sweep 锁：本侧 sweep_once 直退（会话不动）；锁释放后补扫。"""
    from agent_runtime.util import now_ts

    await dual.seed_template()
    scope = scope_of()
    status, _, body = await dual.post(0, "route", session_id="s1")
    assert status == 200, body
    past = now_ts() - 999
    await dual.redis.zadd(f"{SM}session_expiry", {"s1": past})
    await dual.redis.hset(f"{SM}session:s1", "expiry", past)

    lock_key = f"{SM}lock:sweep"
    await dual.redis.set(lock_key, "replica-b", nx=True, ex=30)
    await dual.sysctx(0).sm_sweeper.sweep_once()        # 抢锁失败直退
    assert await dual.redis.scard(
        f"{SM}scope:{scope}:sessions") == 1  # 会话未被误扫

    await dual.redis.delete(lock_key)
    await dual.sysctx(0).sm_sweeper.sweep_once()        # 释放后补扫
    assert await dual.redis.scard(
        f"{SM}scope:{scope}:sessions") == 0


@requires_lua
async def test_concurrent_sweepers_converge_clean(dual):
    """A/B 两侧 sweep_once 并发 gather：无异常、全清、锁正常释放、无重复副作用。"""
    from agent_runtime.util import now_ts

    await dual.seed_template(scope_concurrency=4, pod_concurrency=2)
    scope = scope_of()
    for sid in ("s1", "s2", "s3"):
        status, _, body = await dual.post(0, "route", session_id=sid)
        assert status == 200, body

    past = now_ts() - 999
    for sid in ("s1", "s2", "s3"):
        await dual.redis.zadd(f"{SM}session_expiry", {sid: past})
        await dual.redis.hset(f"{SM}session:{sid}", "expiry", past)

    registered_before = await dual.redis.scard(
        f"{SM}pods:registered")
    await asyncio.gather(
        dual.sysctx(0).sm_sweeper.sweep_once(),
        dual.sysctx(1).sm_sweeper.sweep_once(),
    )

    assert await dual.redis.scard(
        f"{SM}scope:{scope}:sessions") == 0
    assert await dual.redis.zcard(f"{SM}session_expiry") == 0
    assert await dual.redis.exists(f"{SM}lock:sweep") == 0
    # 空扫不误删 Pod 登记（invariant 5）
    assert await dual.redis.scard(
        f"{SM}pods:registered") == registered_before
    await asyncio.sleep(0.2)     # 让 fire-and-forget 的 idle_consider 跑完


# ---------------------------------------------------------------- 三段式契约(容器表拆分)

@requires_lua
async def test_split_contract_sync_on_b_routes_on_a(dual):
    """三段式契约经 B 下发(容器表落库+新形态模板行)→ A 水合同一快照路由成功,
    且 RM cfg 的 deploy_ver 与快照一致(暖复用前提,阶段 11b 同款不变量)。"""
    await dual.seed_template()
    scope = scope_of()
    status, raw, body = await dual.post(0, "route", session_id="sp1")
    assert status == 200, body
    assert raw["pod_id"].startswith("agentserver-")

    # 阶段 11b 同款不变量:快照模板 deploy_ver == RM cfg(暖复用前提)
    from agent_runtime.resource_manager.orchestrator import _deploy_ver
    from agent_runtime.session_manager.routing import snapshot_from_json

    cfg_raw = await dual.redis.hgetall(f"{RM}resource:scope:{scope}:config")
    cfg = {k.decode() if isinstance(k, bytes) else k:
           v.decode() if isinstance(v, bytes) else v
           for k, v in cfg_raw.items()}
    assert _deploy_ver(json.loads(cfg["pod_spec_json"])) == cfg.get("deploy_ver")
    snap = snapshot_from_json(await dual.get(f"{SM}routing:snapshot"))
    assert snap.templates["tpl-1"].deploy_ver() == cfg.get("deploy_ver")
