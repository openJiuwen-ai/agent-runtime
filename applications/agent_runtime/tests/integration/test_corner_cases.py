# coding: utf-8
"""分支覆盖 / Corner case 补充测试（M6 真环境验收后补齐）。

基本功能路径已由 test_route_flow / test_rm_business / test_config_store 覆盖，
本文件专测边界与异常分支：

- SM 编排：参数校验、Pod 清洗后立即 route 的恢复、会话跨 scope 迁移（活跃绑定
  回收）、touch 惰性 evict 唤醒等待者、SM/RM 瞬时漂移 → MaxPodsReached 映射
  NO_POD_AVAILABLE、touch 缺 session_ttl 字段回退默认 TTL；
- RM：acquire 幂等回放零重复部署、他副本持 deploy 锁时等待并复用其成果、
  reclaim 只回收 excess（保护最早 idle 的 min_idle 个）、autoscale 封顶
  max_pods、pod_spec 缺失/损坏跳过、watch 无 pod_ip / 无 sse_port 不误杀；
- 判死枚举 normalize_phase 优先级矩阵；
- config 层：index 优先级 first-fit 匹配矩阵、禁用模板落空回退、模板移除后
  scope 不再命中。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.errors import ConfigNotFound, InvalidParams, NoPodAvailable
from agent_runtime.resource_manager.k8s import normalize_phase
from agent_runtime.resource_manager.models import PodInfo
from agent_runtime.resource_manager.orchestrator import _deploy_ver
from agent_runtime.util import now_ts
from tests.conftest import requires_lua

SCOPE = "scope-main"   # 与 conftest.seed_template 播种的 scope_id 一致


# ---------------------------------------------------------------- SM 编排分支


@requires_lua
async def test_route_and_touch_reject_missing_params(runtime):
    """参数校验分支：session/group/bot/user 任一为空 → InvalidParams(400)。"""
    await runtime.seed_template()
    with pytest.raises(InvalidParams):
        await runtime.orchestrator.route(
            request_id="r1", session_id="", group_id="grp", bot_id="bot",
            user_id="u")
    with pytest.raises(InvalidParams):
        await runtime.orchestrator.route(
            request_id="r2", session_id="s", group_id="", bot_id="bot",
            user_id="u")
    with pytest.raises(InvalidParams):
        await runtime.orchestrator.route(
            request_id="r3", session_id="s", group_id="grp", bot_id="",
            user_id="u")
    with pytest.raises(InvalidParams):
        await runtime.orchestrator.route(
            request_id="r4", session_id="s", group_id="grp", bot_id="bot",
            user_id="")
    with pytest.raises(InvalidParams):
        await runtime.orchestrator.touch("")


@requires_lua
async def test_route_after_pod_death_cleanup_deploys_new(runtime):
    """Pod 被 notify_pod_dead 原子清洗（zset + info 同删）后立即 route：
    重跑仲裁 → need_acquire → 部署新 Pod，不残留不崩溃。"""
    await runtime.seed_template()
    first = await runtime.route("sess_1")
    await runtime.sm_facade.notify_pod_dead(first["pod_id"])
    second = await runtime.route("sess_2")
    assert second["pod_id"] != first["pod_id"]
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == [second["pod_id"]]


@requires_lua
async def test_session_moving_scope_recycles_active_binding(runtime):
    """同 session_id 路由到新 scope（活跃未过期）：旧 scope 绑定被就地回收
    （LUA_ROUTE_PLACE 分支 3，scope 变化路径），新 scope 正常部署。"""
    await runtime.seed_template()                      # 通配兜底 scope → tpl-1
    # 追加一个按 group 命中的 scope(index 0 优先于兜底)
    await runtime.config_store.config_sync({
        "templates": [{"template_id": "tpl-1", "agent_image": "agentserver:1.0",
                       "namespace": "default"}],
        "scopes": [
            {"scope_id": "scope-ga", "index": 0, "template_id": "tpl-1",
             "routing_rules": [{"expressions": [
                 {"field": "group_id", "op": "in", "values": ["ga"]}]}]},
            {"scope_id": SCOPE, "index": 100, "template_id": "tpl-1",
             "routing_rules": []},
        ],
    })
    scope2 = "scope-ga"

    first = await runtime.route("sess_1", group_id="grp", request_id="req-move-1")
    second = await runtime.route("sess_1", group_id="ga",
                                 request_id="req-move-2")   # 活跃绑定跨 scope 迁移

    assert second["pod_id"] != first["pod_id"]
    assert await runtime.sm_state.redis.scard(
        runtime.sm_state.k.scope_sessions(SCOPE)) == 0       # 旧 scope 已回收
    assert await runtime.sm_state.redis.scard(
        runtime.sm_state.k.scope_sessions(scope2)) == 1      # 新 scope 占位
    binding = await runtime.sm_state.redis.hgetall(
        runtime.sm_state.k.session("sess_1"))
    pod_field = binding.get("pod_id") or binding.get(b"pod_id")
    pod_field = pod_field.decode() if isinstance(pod_field, bytes) else pod_field
    assert pod_field == second["pod_id"]


@requires_lua
async def test_touch_expired_session_evicts_and_wakes_waiter(runtime):
    """touch 已过期会话：返回 False + 惰性 evict + PUBLISH free →
   阻塞中的 route 被唤醒并占到刚释放的额度（touch 侧唤醒集成）。"""
    await runtime.seed_template(scope_concurrency=1, pod_concurrency=1)
    first = await runtime.route("sess_1")
    runtime.orchestrator.scope_full_timeout = 5

    async def _expire_and_touch():
        await asyncio.sleep(0.3)
        past = now_ts() - 1
        await runtime.sm_state.redis.zadd(
            runtime.sm_state.k.session_expiry(), {"sess_1": past})
        await runtime.sm_state.redis.hset(
            runtime.sm_state.k.session("sess_1"), "expiry", past)
        assert await runtime.orchestrator.touch("sess_1") is False

    task = asyncio.get_running_loop().create_task(_expire_and_touch())
    result = await runtime.route("sess_2")            # 阻塞 → 被 touch 唤醒
    await task
    assert result["pod_id"] == first["pod_id"]
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.session("sess_1")) == 0


@requires_lua
async def test_route_maps_rm_drift_max_pods_to_no_pod_available(runtime):
    """SM/RM 瞬时漂移（SM 候选被清而 RM 池已满）→ acquire 抛 MaxPodsReached
   → route 映射 NO_POD_AVAILABLE(503)，而非无限等待。"""
    await runtime.seed_template(scope_concurrency=4, pod_concurrency=2)  # max_pods=2
    await runtime.route("s1")
    await runtime.route("s2")                          # pod_1 2/2

    async def clear_candidates():
        """模拟 SM 候选集瞬时漂移（被并发清理）：只清 zset，不动 RM 池。"""
        for pod in await runtime.sm_state.scope_pod_ids(SCOPE):
            await runtime.sm_state.redis.zrem(
                runtime.sm_state.k.scope_pods(SCOPE), pod)

    await clear_candidates()                           # 模拟漂移：候选丢失
    third = await runtime.route("s3")                  # need_acquire → 补 deploy pod_2
    await clear_candidates()                           # 再次漂移
    with pytest.raises(NoPodAvailable):                # RM 池满（2/2）→ 503
        await runtime.route("s4")


@requires_lua
async def test_touch_falls_back_to_default_ttl(runtime):
    """session HASH 缺 session_ttl 字段（异常残留）→ LUA_TOUCH 回退默认 TTL。"""
    await runtime.seed_template()
    await runtime.route("sess_1")
    await runtime.sm_state.redis.hdel(
        runtime.sm_state.k.session("sess_1"), "session_ttl")
    before = now_ts()
    assert await runtime.orchestrator.touch("sess_1") is True
    expiry = await runtime.sm_state.redis.zscore(
        runtime.sm_state.k.session_expiry(), "sess_1")
    assert before + 60 <= expiry <= now_ts() + 61      # default_session_ttl=60


# ---------------------------------------------------------------- RM 分支


@requires_lua
async def test_acquire_idempotent_replay_does_not_redeploy(runtime):
    """同 request_id 重试 acquire：回放缓存结果，零重复部署。"""
    await runtime.seed_template()
    _, template = await runtime.config_store.resolve("user", "grp", "bot")
    kwargs = dict(scope_id=SCOPE, pod_spec=template.deploy_subset(),
                  pool_config=template.pool_config(), request_id="acq-1")
    first = await runtime.rm_facade.acquire(**kwargs)
    second = await runtime.rm_facade.acquire(**kwargs)
    assert second["pod_id"] == first["pod_id"]
    assert len(runtime.k8s.pods) == 1


@requires_lua
async def test_acquire_waits_out_deploy_lock_and_reuses(runtime):
    """他副本持 per-scope deploy 锁：本侧占位 → 抢锁失败短暂等待 →
   锁释放 + 暖 Pod 就绪 → 复用其成果（零部署、占位清干净）。"""
    await runtime.seed_template()
    _, template = await runtime.config_store.resolve("user", "grp", "bot")
    lock_key = runtime.rm_state.k.lock_deploy(SCOPE)
    assert await runtime.rm_state.redis.set(
        lock_key, "other-replica", nx=True, ex=30)

    async def _finish_other_deploy():
        await asyncio.sleep(0.6)
        await runtime.rm_state.register_pod(
            pod_id="pod-other", scope_id=SCOPE,
            pod_sse_url="http://10.42.0.9:8080/sse", pod_ip="10.42.0.9",
            namespace="default",
            deploy_ver=_deploy_ver(template.deploy_subset()),
            deploy_token="other-token", idle_flag=True, now=now_ts())
        await runtime.rm_state.redis.delete(lock_key)

    task = asyncio.get_running_loop().create_task(_finish_other_deploy())
    result = await runtime.rm_facade.acquire(
        scope_id=SCOPE, pod_spec=template.deploy_subset(),
        pool_config=template.pool_config(), request_id="acq-busy")
    await task
    assert result["pod_id"] == "pod-other"
    assert len(runtime.k8s.pods) == 0                  # 本侧零部署
    assert await runtime.rm_state.deploying_count(SCOPE) == 0


@requires_lua
async def test_reclaim_keeps_oldest_min_idle_and_recycles_excess(runtime):
    """idle 池超出 min_idle 底数：只回收 excess（ aged ≥ pod_ttl 的），
   保留最早转入 idle 的 min_idle 个作保底热备。"""
    now = now_ts()
    await runtime.rm_state.save_scope_config(SCOPE, {
        "min_idle_pods": 1, "max_pods": 5, "pod_ttl": 10,
        "deploy_ver": "v1", "pod_spec_json": "{}"})
    for i, pod in enumerate(("pod-old", "pod-mid", "pod-new")):
        await runtime.rm_state.register_pod(
            pod_id=pod, scope_id=SCOPE, pod_sse_url=f"http://{pod}:8080/sse",
            pod_ip=f"10.42.0.{i}", namespace="default", deploy_ver="v1",
            deploy_token=f"t{i}", idle_flag=True, now=now)
        await runtime.rm_state.redis.set(
            runtime.rm_state.k.pod_idle_since(pod), now - 100 + i * 10)

    await runtime.rm_sweeper.reclaim_once()

    assert await runtime.rm_state.idle_pods(SCOPE) == ["pod-old"]   # 保底热备
    assert set(runtime.k8s.deleted) == {"pod-mid", "pod-new"}
    assert await runtime.rm_state.pod_count(SCOPE) == 1


@requires_lua
async def test_autoscale_stops_at_max_pods(runtime):
    """min_idle=2 但 max_pods=2：补满即停，占位不越过上限。"""
    await runtime.seed_template(scope_concurrency=2, pod_concurrency=1,
                                min_idle_pods=2)      # max_pods=2
    await runtime.route("sess_1")                     # 1 在用
    await runtime.rm_sweeper.autoscale_once()         # 补 1 热备 → 池满
    await runtime.rm_sweeper.autoscale_once()         # 已达 max_pods → 不再补
    assert len(await runtime.rm_state.idle_pods(SCOPE)) == 1
    assert await runtime.rm_state.pod_count(SCOPE) == 2
    assert await runtime.rm_state.deploying_count(SCOPE) == 0


@requires_lua
async def test_autoscale_skips_scope_without_valid_pod_spec(runtime):
    """scope config 的 pod_spec_json 缺失/损坏 → autoscale 跳过（不抛、不部署）。"""
    await runtime.rm_state.save_scope_config(SCOPE, {
        "min_idle_pods": 1, "max_pods": 2, "pod_ttl": 300,
        "deploy_ver": "v1", "pod_spec_json": "{not-json"})
    await runtime.rm_sweeper.autoscale_once()
    assert await runtime.rm_state.deploying_count(SCOPE) == 0
    assert len(runtime.k8s.pods) == 0


@requires_lua
async def test_watch_skips_health_probe_without_ip_or_port(runtime):
    """健康探测前置分支：pod_ip 缺失 / scope config 无 sse_port → 跳过探测，
   即便探测目标实际不健康也不误杀。"""
    await runtime.rm_state.save_scope_config(SCOPE, {
        "min_idle_pods": 0, "max_pods": 5, "pod_ttl": 300,
        "deploy_ver": "v1", "pod_spec_json": "{}"})   # 无 sse_port
    for pod, ip in (("pod-noip", ""), ("pod-noport", "10.42.0.1")):
        await runtime.rm_state.register_pod(
            pod_id=pod, scope_id=SCOPE, pod_sse_url=f"http://{ip}:8080/sse",
            pod_ip=ip, namespace="default", deploy_ver="v1",
            deploy_token=pod, idle_flag=False, now=now_ts())
        runtime.k8s.pods[("default", pod)] = PodInfo(
            pod_id=pod, namespace="default", phase="Running", ready=True,
            pod_ip=ip)
    runtime.k8s.unhealthy_pods.add("10.42.0.1")

    await runtime.rm_sweeper.watch_once()

    assert set(await runtime.rm_state.all_pod_ids()) == {"pod-noip", "pod-noport"}


# ---------------------------------------------------------------- 判死枚举


def test_normalize_phase_priority_matrix():
    """归一化优先级：Terminating > 判死等待原因 > phase；Pending 不判死。"""
    assert normalize_phase("Running", True, []) == "Terminating"
    assert normalize_phase("Pending", False, ["ImagePullBackOff"]) == "ImagePullBackOff"
    assert normalize_phase("Running", False, ["ErrImagePull"]) == "ErrImagePull"
    assert normalize_phase("Running", False, ["CrashLoopBackOff"]) == "CrashLoopBackOff"
    assert normalize_phase("Pending", False, ["ContainerCreating"]) == "Pending"
    assert normalize_phase("Pending", False, []) == "Pending"     # Pending 不判死
    assert normalize_phase("", False, []) == "Unknown"


# ---------------------------------------------------------------- config 层


@requires_lua
async def test_resolve_index_priority_first_fit_matrix(runtime):
    """index 从小到大 first-fit 矩阵:index 顺序 / AND / not_in / 通配兜底。"""
    await runtime.config_store.config_sync({
        "templates": [
            {"template_id": "tpl-vip", "agent_image": "a:vip",
             "namespace": "default", "session_ttl": 61},
            {"template_id": "tpl-ban", "agent_image": "a:ban",
             "namespace": "default", "session_ttl": 62},
            {"template_id": "tpl-fb", "agent_image": "a:fb",
             "namespace": "default", "session_ttl": 63},
        ],
        "scopes": [
            # index 0:vip = user 白名单 AND group 白名单 AND bot 不在黑名单
            {"scope_id": "s-vip", "index": 0, "template_id": "tpl-vip",
             "routing_rules": [{"expressions": [
                 {"field": "user_id", "op": "in", "values": ["u-admin"]},
                 {"field": "group_id", "op": "in", "values": ["gg"]},
                 {"field": "bot_id", "op": "not_in", "values": ["bb-banned"]},
             ]}]},
            # index 10:ban 组命中但 user 在封禁名单 → 不命中
            {"scope_id": "s-ban", "index": 10, "template_id": "tpl-ban",
             "routing_rules": [{"expressions": [
                 {"field": "group_id", "op": "in", "values": ["gg"]},
                 {"field": "user_id", "op": "not_in", "values": ["u-banned"]},
             ]}]},
            # index 100:通配兜底
            {"scope_id": "s-fb", "index": 100, "template_id": "tpl-fb",
             "routing_rules": []},
        ],
    })

    async def _hit(user, group, bot):
        scope_id, template = await runtime.config_store.resolve(user, group, bot)
        return scope_id, template.template_id

    # 三表达式全真 → vip
    assert await _hit("u-admin", "gg", "bb") == ("s-vip", "tpl-vip")
    # bot 被拉黑 → 落到 s-ban(group 命中且 user 未封禁)
    assert await _hit("u-admin", "gg", "bb-banned") == ("s-ban", "tpl-ban")
    # user 被封禁 → s-ban 不命中 → 通配兜底
    assert await _hit("u-banned", "gg", "bb") == ("s-fb", "tpl-fb")
    # 无任何规则命中 → 兜底
    assert await _hit("u-x", "gx", "bx") == ("s-fb", "tpl-fb")


@requires_lua
async def test_resolve_skips_disabled_template_and_falls_back(runtime):
    """enabled=False 的模板视为未命中 → 落到下一个 index 的 scope。"""
    await runtime.config_store.config_sync({
        "templates": [
            {"template_id": "tpl-off", "agent_image": "a:off",
             "namespace": "default", "enabled": False},
            {"template_id": "tpl-ok", "agent_image": "a:ok",
             "namespace": "default"},
        ],
        "scopes": [
            {"scope_id": "s-off", "index": 0, "template_id": "tpl-off",
             "routing_rules": []},
            {"scope_id": "s-ok", "index": 100, "template_id": "tpl-ok",
             "routing_rules": []},
        ],
    })
    scope_id, template = await runtime.config_store.resolve("u", "g", "b")
    assert (scope_id, template.template_id) == ("s-ok", "tpl-ok")


@requires_lua
async def test_template_removed_from_payload_scope_stops_matching(runtime):
    """全量下发移除模板与 scope → 下一次 route CONFIG_NOT_FOUND。"""
    await runtime.seed_template()
    await runtime.route("sess_1")

    result = await runtime.config_store.config_sync(
        {"templates": [], "scopes": []})

    assert result["templates_deleted"] == 1 and result["scopes_deleted"] == 1
    with pytest.raises(ConfigNotFound):
        await runtime.orchestrator.route(
            request_id="r-after-del", session_id="sess_2",
            group_id="grp", bot_id="bot", user_id="u")
