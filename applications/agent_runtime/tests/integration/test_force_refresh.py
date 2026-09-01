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
- R3  刷新排空期内下发的守卫行为(B 类放行 / A 类按日落中间态 409,排空后放行)
- R4  重建使用 RM 缓存的存量 pod_spec(配置零变化)
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
    """R3:刷新排空期内的下发守卫——B 类(同版本)放行;A 类(换版本)按既有
    日落中间态语义 409,老代 Pod 回收后放行。

    守卫按版本判定(不看代次):老代 Pod 版本与当前配置一致 → B 类不可见;
    A 类新版本 ≠ 老 Pod 版本 → 可见 → 409(与 M 期 A-类叠 A-类行为一致,
    防不可归因的混合态;B 类不受阻 = C1a/C1b 同款病理边界)。
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

    # A 类(镜像变 → deploy_ver 变)→ 日落中间态 409(老代 Pod 版本可见)
    with pytest.raises(ConfigSyncBusy):
        await runtime.config_store.config_sync(_sync_payload(
            _tpl(agent_image="agentserver:2.0", pod_ttl=2)))

    # 老代回收后 → A 类放行,池收敛到新版本
    await asyncio.sleep(2.2)
    await runtime.rm_sweeper.reclaim_once()
    assert old_pod not in await runtime.rm_state.all_pod_ids()
    a = await runtime.config_store.config_sync(_sync_payload(
        _tpl(agent_image="agentserver:2.0", pod_ttl=2)))
    assert a["ok"] is True
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
