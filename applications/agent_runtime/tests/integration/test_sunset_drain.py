# coding: utf-8
"""config_refresh / config_sync A 类日落的优雅排空窗口(2026-09-17)集成网。

此前语义:stale(ver/gen 落后)Pod 免老化即刻回收——带活跃会话也被硬切
(wangchang 2026-09-17 实录:会话进行 57s 被 5s 内回收)。现在:日落 Pod
保留 drain_until = 日落 + session_ttl,期间已绑定会话继续服务(route 亲和
与 touch 只查 pod:info,不查候选集);配套 max_pods surge 余量 +1 让补位
Pod 有槽可部署;排空收完 reclaim 主动 DEL drain_until 精确终止 surge。

方法论同 test_force_refresh.py:真实业务路径 + TTL 调小自然到期,不回拨
指针(排空兜底用例的 crash 注入除外——DEL drain 键模拟的是进程死透后的
键过期残局,非时间操纵)。
"""

from __future__ import annotations

import asyncio

from tests.conftest import Runtime, requires_lua

SCOPE = "scope-main"


@requires_lua
async def test_drain_window_keeps_serving_sessions(runtime):
    """D1:排空窗口内老 Pod 不回收,已绑定会话路由/touch 全程连续。"""
    await runtime.seed_template(min_idle_pods=0, session_ttl=60, pod_ttl=60)
    first = await runtime.route("sess_1")
    pod = first["pod_id"]

    await runtime.config_store.config_refresh()        # 日落 + 戳排空纪元
    assert await runtime.rm_state.drain_until(SCOPE) is not None

    # 会话连续性:亲和命中老 Pod(不查候选集),touch 保活正常
    again = await runtime.route("sess_1")
    assert again["pod_id"] == pod
    assert await runtime.orchestrator.touch("sess_1") is True

    # 窗口内(≪ session_ttl=60):reconcile 释放入 idle 后 reclaim 也不收
    await runtime.rm_sweeper.reconcile_once()
    await runtime.rm_sweeper.reclaim_once()
    assert pod in await runtime.rm_state.all_pod_ids()
    assert await runtime.rm_state.drain_until(SCOPE) is not None


@requires_lua
async def test_surge_margin_funds_replacement_under_max_pods(runtime):
    """D2:surge 余量 +1——max_pods=1 且唯一 Pod 排空中,新会话仍能部署补位。

    这是排空窗口的配套头寸:若无 surge,排空 Pod 占着唯一槽位,新流量在
    整个窗口内 503(2026-09-17 早间分析确认的容量保护场景)。"""
    await runtime.seed_template(
        min_idle_pods=0, scope_concurrency=2, pod_concurrency=2,
        session_ttl=60, pod_ttl=60,
    )   # ⌈2/2⌉=1:唯一槽位,但 SM 容许 2 并发会话(第二会话可走到 RM)
    first = await runtime.route("sess_1")              # 唯一槽位被占(忙)
    old_pod = first["pod_id"]

    await runtime.config_store.config_refresh()        # 日落老 Pod + surge 激活

    # 新会话(无绑定):无暖 Pod → need_deploy;total=1 ≥ max_pods=1,
    # 仅靠 surge cap=2 放行部署
    second = await runtime.route("sess_2")
    new_pod = second["pod_id"]
    assert new_pod != old_pod
    assert len(await runtime.rm_state.all_pod_ids()) == 2
    # 老会话仍在老 Pod 上;排空纪元未收尾(surge 持续)
    assert (await runtime.route("sess_1"))["pod_id"] == old_pod
    assert await runtime.rm_state.drain_until(SCOPE) is not None


@requires_lua
async def test_b_class_push_does_not_extend_drain_window(runtime):
    """D3:排空纪元由首因下发(refresh bump / A 类换版)定窗,B 类纯池参数
    下发不重戳不延长。"""
    await runtime.seed_template(min_idle_pods=0, session_ttl=60, pod_ttl=60)
    await runtime.route("sess_1")
    await runtime.config_store.config_refresh()
    before = await runtime.rm_state.drain_until(SCOPE)
    assert before is not None

    from tests.conftest import split_sync_payload
    await runtime.config_store.config_sync(split_sync_payload(
        [{"template_id": "tpl-1", "agent_image": "agentserver:1.0",
          "namespace": "default", "scope_concurrency": 3,
          "pod_concurrency": 2, "session_ttl": 60, "pod_ttl": 99,
          "min_idle_pods": 0}],                       # 仅 pod_ttl 变(B 类)
        [{"scope_id": SCOPE, "index": 0,
          "template_id": "tpl-1", "routing_rules": ""}],
    ))
    assert await runtime.rm_state.drain_until(SCOPE) == before   # 窗口不动


@requires_lua
async def test_missing_drain_key_falls_back_to_immediate(runtime):
    """D4:drain 键缺失(进程死透后 TTL 过期的残局)兜底为立即回收——
    闸门收敛保证不因排空语义被破坏(不会永久 409)。"""
    await runtime.seed_template(min_idle_pods=0, session_ttl=60, pod_ttl=60)
    first = await runtime.route("sess_1")
    pod = first["pod_id"]
    await runtime.config_store.config_refresh()
    await runtime.rm_sweeper.reconcile_once()          # 释放入 idle(stale)

    # crash 注入:排空键过期消失(非时间操纵,模拟键 TTL 兜底后的残局)
    await runtime.rm_state.clear_drain_until(SCOPE)
    await runtime.rm_sweeper.reclaim_once()
    assert pod not in await runtime.rm_state.all_pod_ids()
