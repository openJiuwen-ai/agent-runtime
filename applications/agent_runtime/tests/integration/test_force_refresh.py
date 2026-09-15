# coding: utf-8
"""config_refresh(强制刷新,场景 M-R)自然老化集成网。

方法论与 test_audit_repro.py 一致(与 e2e 覆盖硬标准一致):
- 只走真实业务路径(config_sync / config_refresh / route / touch / 真实后台
  tick),TTL 调小自然到期,**不回拨指针、不直改 Redis 键**;
- 断言完整日落闭环:软摘除 → 亲和保持 → 自然转 idle → reclaim 按代次回收 →
  autoscale 按存量配置重建(新 Pod 烙新代次)。

用例:
- R1  全量自然周期(日落 → 排空 → 重建)
- R2  重复刷新收敛(非幂等但终态唯一)
- R3  刷新排空期内下发的守卫行为(B/A 类均放行;守卫基准=当前生效版本)
- R4  重建使用 RM 缓存的存量 pod_spec(配置零变化)
- R5  refresh 串行闸门(老代回收前再刷 409,回收后放行;判据=代次)
- R6  reclaim 两级回收:ver/gen 落后免老化即刻回收(③)
- R7  reclaim 两级回收:当前版本超额仍 aged ≥ pod_ttl(③ 对照)
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.errors import ConfigSyncBusy
from tests.conftest import Runtime, requires_lua

SCOPE = "scope-main"


def _tpl(template_id: str = "tpl-1", **overrides) -> dict:
    base = {
        "agent_image": "agentserver:1.0",
        "namespace": "default",
        "scope_concurrency": 3,
        "pod_concurrency": 2,
        "session_ttl": 60,
        "pod_ttl": 300,
        "min_idle_pods": 0,
        "max_pods": 5,
    }
    base.update(overrides)
    return {"template_id": template_id, **base}


def _sync_payload(template: dict) -> dict:
    from tests.conftest import split_sync_payload

    return split_sync_payload(
        [template],
        [{"scope_id": SCOPE, "index": 0,
          "template_id": template["template_id"], "routing_rules": ""}],
    )


async def _natural_idle(runtime: Runtime, session: str = "sess_1",
                        ttl_wait: float = 1.6) -> str:
    """等会话自然到期 → 驱动真实 sweep tick → 等 idle_consider 落 RM idle 池。"""
    await asyncio.sleep(ttl_wait)
    await runtime.sm_sweeper.sweep_once()
    for _ in range(100):
        idle = await runtime.rm_state.idle_pods(SCOPE)
        if idle:
            return idle[0]
        await asyncio.sleep(0.02)
    raise AssertionError("pod never transitioned to idle via real lifecycle")


# -------------------------------------------------------------- R1:全量自然周期

@requires_lua
async def test_R1_full_natural_cycle_drain_and_rebuild(runtime):
    """R1:刷新 → 存量会话亲和保持 → 老 Pod 自然到期转 idle → reclaim 按代次
    回收(K8s 删 + PURGE + SM 清注册)→ autoscale 重建带新代次的暖 Pod。"""
    await runtime.seed_template(min_idle_pods=1, session_ttl=1, pod_ttl=2)
    first = await runtime.route("sess_1")
    old_pod = first["pod_id"]

    result = await runtime.config_store.config_refresh()
    assert result["generations"] == {SCOPE: 1}
    assert result["pods_sunset"] == 1
    # 亲和:老 Pod 继续服务存量会话(不查候选集)
    again = await runtime.route("sess_1")
    assert again["pod_id"] == old_pod
    assert await runtime.orchestrator.touch("sess_1") is True

    # 自然到期 → idle → 真等过 pod_ttl → reclaim 回收老代
    idle_pod = await _natural_idle(runtime)
    assert idle_pod == old_pod
    await asyncio.sleep(2.2)
    await runtime.rm_sweeper.reclaim_once()
    assert old_pod not in await runtime.rm_state.all_pod_ids()
    assert old_pod in runtime.k8s.deleted
    assert f"{SCOPE}:{old_pod}" not in await runtime.sm_state.registered_pods()

    # autoscale 重建:新 Pod 烙当前代次,入 idle 暖池
    await runtime.rm_sweeper.autoscale_once()
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    idle = await runtime.rm_state.idle_pods(SCOPE)
    assert len(idle) == 1
    new_info = await runtime.rm_state.pod_info(idle[0])
    assert new_info["generation"] == cfg["generation"] == "1"
    assert new_info["deploy_ver"] == cfg["deploy_ver"]


# -------------------------------------------------------------- R2:重复刷新收敛

@requires_lua
async def test_R2_repeat_refresh_converges(runtime):
    """R2:两次刷新(非幂等)→ 代次递增,每轮老代回收、新代重建,终态收敛为
    仅最新代 warm Pod(交错驱动,max_pods 默认 2 内部署)。"""
    await runtime.seed_template(min_idle_pods=1, session_ttl=60, pod_ttl=1)

    await runtime.rm_sweeper.autoscale_once()            # P1(gen "")
    p1 = (await runtime.rm_state.all_pod_ids())[0]

    r1 = await runtime.config_store.config_refresh()     # gen 1
    await runtime.rm_sweeper.autoscale_once()            # P2(gen 1)
    await asyncio.sleep(1.2)                             # 真等过 pod_ttl=1
    await runtime.rm_sweeper.reclaim_once()
    assert p1 not in await runtime.rm_state.all_pod_ids()

    r2 = await runtime.config_store.config_refresh()     # gen 2
    await runtime.rm_sweeper.autoscale_once()            # P3(gen 2)
    cfg_gen = (await runtime.rm_state.load_scope_config(SCOPE))["generation"]

    await asyncio.sleep(1.2)
    await runtime.rm_sweeper.reclaim_once()
    survivors = await runtime.rm_state.all_pod_ids()
    assert len(survivors) == 1                           # 仅最新代 warm 存活
    assert (await runtime.rm_state.pod_info(survivors[0]))["generation"] == cfg_gen
    assert r1["generations"] == {SCOPE: 1} and r2["generations"] == {SCOPE: 2}


# -------------------------------------------------------------- R3:守卫交互

@requires_lua
async def test_R3_refresh_then_sync_guard_semantics(runtime):
    """R3:刷新排空期内的下发守卫——基准=当前生效版本,B/A 类均放行。

    2026-09-15 修正:原判据比**新载荷**版本——A 类(换版本)时当前代空闲 Pod
    必然 ≠ 新版被误判日落遗留;而它 ver==cfg 受 min_idle 底数保护永不回收,
    等它 = 配置面永久 409(cyz 实测:暖 Pod idle 33min ≫ pod_ttl 仍 409)。
    修正后与 refresh 闸门比当前代次同构,只拦 ver ≠ 当前配置的真版本遗留
    (见 test_config_sync_rejects_when_sunset_pending)。A 类放行后由扩散②
    软摘 + reclaim 免老化即刻回收老代 Pod(③,无需等 pod_ttl)。
    """
    await runtime.seed_template(
        agent_image="agentserver:1.0", session_ttl=1, pod_ttl=2)
    await runtime.route("sess_1")
    await runtime.config_store.config_refresh()
    old_pod = (await _natural_idle(runtime))             # 老代 idle(会话到期)

    # B 类(同 deploy_ver,策略字段变;pod_ttl 保持 2,否则会把回收龄改大)→ 放行
    b = await runtime.config_store.config_sync(_sync_payload(
        _tpl(agent_image="agentserver:1.0", session_ttl=99, pod_ttl=2)))
    assert b["ok"] is True

    # A 类(镜像变 → deploy_ver 变)→ 同样放行(老代 Pod ver==当前配置,不可见)
    a = await runtime.config_store.config_sync(_sync_payload(
        _tpl(agent_image="agentserver:2.0", pod_ttl=2)))
    assert a["ok"] is True

    # 老代 Pod:ver/gen 均落后于新 cfg → stale 免老化即刻回收(无需 sleep 过 pod_ttl)
    await runtime.rm_sweeper.reclaim_once()
    assert old_pod not in await runtime.rm_state.all_pod_ids()
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert "agentserver:2.0" in cfg["pod_spec_json"]


# -------------------------------------------------------------- R4:重建用存量 spec

@requires_lua
async def test_R4_rebuild_uses_cached_pod_spec(runtime):
    """R4:重建部署的 pod_spec 与 RM 缓存逐字段一致(刷新不改配置,仅换代)。"""
    await runtime.seed_template(min_idle_pods=1, agent_image="agentserver:7.7")
    await runtime.rm_sweeper.autoscale_once()
    spec_before = runtime.k8s.deployed_specs[-1]

    await runtime.config_store.config_refresh()
    await runtime.rm_sweeper.autoscale_once()            # 重建

    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert runtime.k8s.deployed_specs[-1] == json.loads(cfg["pod_spec_json"])
    assert runtime.k8s.deployed_specs[-1] == spec_before   # 配置零变化


# ------------------------------------------------------- R5:refresh 串行闸门

@requires_lua
async def test_R5_refresh_serialized_until_sunset_done(runtime):
    """R5:refresh 前置日落闸门——老代 Pod 回收前再刷 409(零副作用),回收后放行。

    闸门判据=代次(refresh 不改 deploy_ver,config_sync 的版本判定对 refresh
    日落失明);回收由 reclaim 代次感知保证收敛 → 闸门不会永久 409。
    病理实录:2026-09-11 wangchang 环境 9 分钟 4 连刷(gen 2/3/4/5),多代
    日落堆积蹲占 max_pods=2 把滚动窗口焊死 2.5 分钟。
    """
    await runtime.seed_template(min_idle_pods=1, session_ttl=60, pod_ttl=1)

    await runtime.rm_sweeper.autoscale_once()            # P1(gen "")
    p1 = (await runtime.rm_state.all_pod_ids())[0]
    r1 = await runtime.config_store.config_refresh()     # gen 1:P1 日落
    await runtime.rm_sweeper.autoscale_once()            # P2(gen 1)
    assert r1["generations"] == {SCOPE: 1}

    # P1(gen "")代次落后且未回收 → 立即再刷 409;拒绝时零副作用(gen 仍 1)
    with pytest.raises(ConfigSyncBusy, match="pending reclaim"):
        await runtime.config_store.config_refresh()
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg["generation"] == "1"
    assert runtime.gen_bumps == [SCOPE]

    # 自然回收 P1(idle 已过 pod_ttl=1)→ 闸门放行,gen 2
    await asyncio.sleep(1.2)
    await runtime.rm_sweeper.reclaim_once()
    assert p1 not in await runtime.rm_state.all_pod_ids()
    r2 = await runtime.config_store.config_refresh()
    assert r2["generations"] == {SCOPE: 2}
    assert runtime.gen_bumps == [SCOPE, SCOPE]


# ---------------------------------------------------- R6/R7:reclaim 两级回收(③)

@requires_lua
async def test_R6_reclaim_stale_immediate_no_aging(runtime):
    """R6(③ 2026-09-15):ver/gen 落后的 idle Pod 免老化即刻回收。

    pod_ttl=60 远未到龄,老代 Pod(gen 落后)转 idle 后同拍回收——旧版/旧代
    被 acquire 的 want_ver+generation 过滤判死,零复用价值,蹲满剩余 pod_ttl
    只白占 max_pods 槽位并拖长 config_sync/refresh 日落闸门的等待。
    当前版本超额的老化义务见 R7。"""
    await runtime.seed_template(min_idle_pods=1, session_ttl=1, pod_ttl=60)
    await runtime.route("sess_1")
    await runtime.config_store.config_refresh()
    old_pod = await _natural_idle(runtime)       # 老代 idle,aged ≈ 0 ≪ pod_ttl=60
    await runtime.rm_sweeper.reclaim_once()      # 同拍回收,不等 pod_ttl
    assert old_pod not in await runtime.rm_state.all_pod_ids()


@requires_lua
async def test_R7_reclaim_warm_overflow_still_ages(runtime):
    """R7(③ 对照):当前版本超额(超 min_idle 底数)仍须 aged ≥ pod_ttl——
    抗「马上会被复用」的抖动;即刻回收只给零复用价值的落后 Pod。"""
    await runtime.seed_template(
        min_idle_pods=1, pod_concurrency=1, session_ttl=1, pod_ttl=2)
    await runtime.route("sess_a")
    await runtime.route("sess_b")                # pc=1 → 各占一只 Pod
    await asyncio.sleep(1.6)                     # 会话自然到期
    await runtime.sm_sweeper.sweep_once()        # 空 Pod pass → 双双转 idle
    for _ in range(100):
        if len(await runtime.rm_state.idle_pods(SCOPE)) >= 2:
            break
        await asyncio.sleep(0.02)
    both = set(await runtime.rm_state.all_pod_ids())
    assert len(both) == 2
    await runtime.rm_sweeper.reclaim_once()      # 未到龄 → 全保留(底数 + 抗抖)
    assert set(await runtime.rm_state.all_pod_ids()) == both
    await asyncio.sleep(2.2)                     # 真等过 pod_ttl=2
    await runtime.rm_sweeper.reclaim_once()
    assert len(set(await runtime.rm_state.all_pod_ids())) == 1   # 溢出者走,底数留


# ------------------------------------------- R8:闸门过滤候选集内老代 Pod(竞态)

@requires_lua
async def test_R8_refresh_gate_skips_busy_lagged_pod_in_candidates(runtime):
    """R8(2026-09-15):日落 ZREM 与并发 follower 复用的 REGISTER_POD 竞态把
    老代 Pod 重新登记回候选集 → 闸门不拦在集老代 Pod(在集=合法服务中,等它
    =等会话生命周期;e2e 15s 连刷 409 连坐 2min+ 实录)。放行后自愈闭环:
    全量软摘除把它 ZREM 出集 → reconcile 入 idle → reclaim stale 即刻回收。"""
    await runtime.seed_template(min_idle_pods=1, session_ttl=60, pod_ttl=60)
    r1 = await runtime.route("sess_1")
    pod = r1["pod_id"]
    r = await runtime.config_store.config_refresh()     # gen 1:全量软摘除
    assert r["generations"] == {SCOPE: 1}
    # 竞态形态:并发 follower 复用把老代 Pod REGISTER 回候选集(真实 API)
    url = await runtime.sm_state.pod_sse_url(SCOPE, pod)
    ver = await runtime.sm_state.pod_deploy_ver(SCOPE, pod)
    await runtime.sm_state.register_pod(SCOPE, pod, url, ver)
    assert pod in await runtime.sm_state.scope_pod_ids(SCOPE)
    # 老行为:409 等会话结束;修正后:在集不拦 → 放行(gen 2)
    r2 = await runtime.config_store.config_refresh()
    assert r2["generations"] == {SCOPE: 2}
    # 自愈闭环:软摘除摘出 → reconcile release 入 idle → stale 即刻回收
    # (pod_ttl=60 远未到;会话硬切重放置,决策接受)
    await runtime.rm_sweeper.reconcile_once()
    await runtime.rm_sweeper.reclaim_once()
    assert pod not in await runtime.rm_state.all_pod_ids()


# ------------------------------------------- R8:闸门过滤候选集内老代 Pod(竞态)

@requires_lua
async def test_R8_refresh_gate_skips_busy_lagged_pod_in_candidates(runtime):
    """R8(2026-09-15):日落 ZREM 与并发 follower 复用的 REGISTER_POD 竞态把
    老代 Pod 重新登记回候选集 → 闸门不拦在集老代 Pod(在集=合法服务中,等它
    =等会话生命周期;e2e 15s 连刷 409 连坐 2min+ 实录)。放行后自愈闭环:
    全量软摘除把它 ZREM 出集 → reconcile 入 idle → reclaim stale 即刻回收。"""
    await runtime.seed_template(min_idle_pods=1, session_ttl=60, pod_ttl=60)
    r1 = await runtime.route("sess_1")
    pod = r1["pod_id"]
    r = await runtime.config_store.config_refresh()     # gen 1:全量软摘除
    assert r["generations"] == {SCOPE: 1}
    # 竞态形态:并发 follower 复用把老代 Pod REGISTER 回候选集(真实 API)
    url = await runtime.sm_state.pod_sse_url(SCOPE, pod)
    ver = await runtime.sm_state.pod_deploy_ver(SCOPE, pod)
    await runtime.sm_state.register_pod(SCOPE, pod, url, ver)
    assert pod in await runtime.sm_state.scope_pod_ids(SCOPE)
    # 老行为:409 等会话结束;修正后:在集不拦 → 放行(gen 2)
    r2 = await runtime.config_store.config_refresh()
    assert r2["generations"] == {SCOPE: 2}
    # 自愈闭环:软摘除摘出 → reconcile release 入 idle → stale 即刻回收
    # (pod_ttl=60 远未到;会话硬切重放置,决策接受)
    await runtime.rm_sweeper.reconcile_once()
    await runtime.rm_sweeper.reclaim_once()
    assert pod not in await runtime.rm_state.all_pod_ids()
