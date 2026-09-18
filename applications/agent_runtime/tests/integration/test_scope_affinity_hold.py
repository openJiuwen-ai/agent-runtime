# coding: utf-8
"""scope 亲和保持(2026-09-scope-affinity-hold,issue #152)集成网。

此前语义:每次 route 用当前快照重算 first-fit,LUA_ROUTE_PLACE 亲和分支要求
新解析 scope == 绑定 scope——优先级重排/禁用/失权后未到期旧会话被即时迁移
(内联 EVICT + 重放置进新 scope),对话连续性被打断而 Pod 全程未死。

现在:亲和只认「绑定 + Pod 存活」(重算规则只服务新放置);路由性排除变更
(expr 变化/生效→失效/删 scope)由 config_sync bump gen → 走日落排空窗口
(与 #151 同哲学):存量会话窗口内继续原池,Pod 过窗回收后 rebind 落新规则;
新会话立即走新规则。index 重排不触发任何日落——路径 A 零打扰。

方法论同 test_sunset_drain.py:真实业务路径 + TTL 调小自然到期,不回拨指针。
A 路径(重排零迁移)的配置层断言在
test_config_store.py::test_config_sync_index_reorder_no_bump_no_migrate。
"""

from __future__ import annotations

import asyncio

from tests.conftest import requires_lua, split_sync_payload

SCOPE = "scope-main"
FB = "scope-fb"


async def _sync(runtime, main=True, main_enabled=True, main_expr="group_id in ('grp')",
                main_index=0, session_ttl=1) -> None:
    """播种 scope-main(按 group 命中)+ scope-fb(通配兜底),小 TTL 自然到期。"""
    scopes = []
    if main:
        body = {"scope_id": SCOPE, "index": main_index, "template_id": "tpl-1",
                "routing_rules": main_expr}
        if not main_enabled:
            body["enabled"] = False
        scopes.append(body)
    scopes.append({"scope_id": FB, "index": 100, "template_id": "tpl-1",
                   "routing_rules": ""})
    await runtime.config_store.config_sync(split_sync_payload(
        [{"template_id": "tpl-1", "agent_image": "agentserver:1.0",
          "namespace": "default", "scope_concurrency": 3, "pod_concurrency": 2,
          "session_ttl": session_ttl, "pod_ttl": 1, "min_idle_pods": 0}],
        scopes,
    ))


async def _binding_scope(runtime, session_id: str) -> str:
    b = await runtime.sm_state.redis.hgetall(runtime.sm_state.k.session(session_id))
    v = b.get("scope_id") or b.get(b"scope_id")
    return v.decode() if isinstance(v, bytes) else v


@requires_lua
async def test_priority_reorder_zero_disturbance(runtime):
    """H1(路径 A):他人 scope index 重排 → 原 scope 不 bump 不日落,旧会话
    原 Pod 原 scope,新会话 first-fit 落新优先级。"""
    await _sync(runtime, main_expr="group_id in ('ga')", main_index=10,
                session_ttl=60)
    # team(20) 与 main(10) 同时命中 ga;旧会话落 main
    await runtime.config_store.config_sync(split_sync_payload(
        [{"template_id": "tpl-1", "agent_image": "agentserver:1.0",
          "namespace": "default", "scope_concurrency": 3, "pod_concurrency": 2,
          "session_ttl": 60, "pod_ttl": 60, "min_idle_pods": 0}],
        [
            {"scope_id": SCOPE, "index": 10, "template_id": "tpl-1",
             "routing_rules": "group_id in ('ga')"},
            {"scope_id": "team", "index": 20, "template_id": "tpl-1",
             "routing_rules": "group_id in ('ga')"},
        ],
    ))
    first = await runtime.route("sess_1", group_id="ga")
    pod = first["pod_id"]
    runtime.gen_bumps.clear()

    # 重排:team.index 20 → 5(不排除命中,只改偏好序)
    await runtime.config_store.config_sync(split_sync_payload(
        [{"template_id": "tpl-1", "agent_image": "agentserver:1.0",
          "namespace": "default", "scope_concurrency": 3, "pod_concurrency": 2,
          "session_ttl": 60, "pod_ttl": 60, "min_idle_pods": 0}],
        [
            {"scope_id": SCOPE, "index": 10, "template_id": "tpl-1",
             "routing_rules": "group_id in ('ga')"},
            {"scope_id": "team", "index": 5, "template_id": "tpl-1",
             "routing_rules": "group_id in ('ga')"},
        ],
    ))
    assert runtime.gen_bumps == []
    assert await runtime.rm_state.drain_until(SCOPE) is None
    assert await runtime.rm_state.drain_until("team") is None
    # 旧会话零打扰:同 Pod 同 scope;新会话进 team
    assert (await runtime.route("sess_1", group_id="ga"))["pod_id"] == pod
    assert await _binding_scope(runtime, "sess_1") == SCOPE
    await runtime.route("sess_2", group_id="ga")
    assert await _binding_scope(runtime, "sess_2") == "team"


@requires_lua
async def test_disable_scope_drains_and_rebinds(runtime):
    """H2(路径 B,残留 enabled=false ≡ 删除,2026-09-drop-enabled-fields):
    剔除 main → bump+排空纪元;窗口内旧会话继续原 Pod;过窗回收后 rebind 落
    兜底。排空收尾 DEL 纪元。"""
    await _sync(runtime)
    first = await runtime.route("sess_1")               # group=grp → main
    pod = first["pod_id"]
    assert await _binding_scope(runtime, "sess_1") == SCOPE

    runtime.gen_bumps.clear()
    await _sync(runtime, main_enabled=False)            # 残留禁用 → 视为删除(留兜底)
    assert runtime.gen_bumps == [SCOPE]
    assert await runtime.rm_state.drain_until(SCOPE) is not None
    # 窗口内旧会话继续原 Pod(亲和保持);新会话立即兜底
    assert (await runtime.route("sess_1"))["pod_id"] == pod
    await runtime.route("sess_2")
    assert await _binding_scope(runtime, "sess_2") == FB

    # 自然到期:session_ttl=1 → 会话过期 → 空 Pod pass → idle → 过窗 reclaim
    await asyncio.sleep(1.2)
    await runtime.sm_sweeper.sweep_once()
    for _ in range(100):
        if await runtime.rm_state.idle_pods(SCOPE):
            break
        await asyncio.sleep(0.02)
    await runtime.rm_sweeper.reconcile_once()
    await runtime.rm_sweeper.reclaim_once()
    assert pod not in await runtime.rm_state.all_pod_ids()
    assert await runtime.rm_state.drain_until(SCOPE) is None    # 排空收尾
    # 旧会话再来:绑定 Pod 已亡 → rebind → 新规则落兜底
    again = await runtime.route("sess_1")
    assert again["pod_id"] != pod
    assert await _binding_scope(runtime, "sess_1") == FB


@requires_lua
async def test_rules_change_sunsets_candidates(runtime):
    """H3(失权):expr 变化 → bump + 候选集软摘(新会话不落排空中的老代
    Pod);旧会话亲和保持。"""
    await _sync(runtime)
    first = await runtime.route("sess_1")
    runtime.gen_bumps.clear()

    await _sync(runtime, main_expr="group_id in ('other')")   # grp 失权
    assert runtime.gen_bumps == [SCOPE]
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []   # 候选软摘
    assert await runtime.rm_state.drain_until(SCOPE) is not None
    # 旧会话同 Pod;新会话落兜底(而非排空中的 main 老代 Pod)
    assert (await runtime.route("sess_1"))["pod_id"] == first["pod_id"]
    await runtime.route("sess_2")
    assert await _binding_scope(runtime, "sess_2") == FB


@requires_lua
async def test_deleted_scope_bumps_and_drains(runtime):
    """H4(删 scope):bump + min_idle=0 推送(带 session_ttl 供纪元计窗)+
    排空纪元;窗口内旧会话继续原 Pod。"""
    await _sync(runtime)
    first = await runtime.route("sess_1")
    runtime.gen_bumps.clear()
    runtime.pool_pushes.clear()

    await _sync(runtime, main=False)                    # 从载荷中删除 main
    assert runtime.gen_bumps == [SCOPE]
    assert await runtime.rm_state.drain_until(SCOPE) is not None
    drop = [p for p in runtime.pool_pushes if p[0] == SCOPE]
    assert drop and drop[-1][2] is None                 # 无 pod_spec(停预热)
    assert "session_ttl" in drop[-1][1]                 # 纪元计窗依赖
    assert (await runtime.route("sess_1"))["pod_id"] == first["pod_id"]


@requires_lua
async def test_rebind_after_pod_death_replaces_in_resolved_scope(runtime):
    """H5(rebind 回归):绑定 Pod 消失(notify_pod_dead)→ rebind → 按新规则
    重放置(同 scope 部署新 Pod),旧绑定不残留。"""
    await runtime.seed_template()
    first = await runtime.route("sess_1")
    await runtime.sm_facade.notify_pod_dead(first["pod_id"])

    # 独立 request_id:route 默认按 session_id 生成,RM 幂等缓存会回放首次
    # acquire 的旧 Pod(幂等语义正确,与本用例无关)
    second = await runtime.route("sess_1", request_id="req-h5-retry")
    assert second["pod_id"] != first["pod_id"]
    assert await _binding_scope(runtime, "sess_1") == SCOPE
    assert await runtime.sm_state.redis.scard(
        runtime.sm_state.k.scope_sessions(SCOPE)) == 1   # 旧额度已释放
