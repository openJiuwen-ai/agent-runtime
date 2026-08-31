# coding: utf-8
"""审计实锋试验场(2026-08-27 全量排查):用真实流程逐条验证静态审计假设。

方法论(与 e2e 覆盖硬标准一致):
- 只走真实业务路径(config_sync / route / touch / 真实后台 tick),TTL 调小自然到期,
  **不回拨指针、不直改 Redis 键**;
- 竞态窗口类假设用「调度/故障注入」控制时序(包装注入点、按 facade 真实顺序手工驱动
  状态原语),被测业务逻辑零改动;
- 每条用例断言的是**期望的正确行为**:当前若 FAIL = 假设实锤(缺陷存在);
  修复后本文件整体转正为回归网。若某用例当前 PASS,对应假设即被证伪,如实记录。

用例命名 test_<假设编号>_<场景>,编号对应审计报告:
- C1  config_sync 409 日落误判(把 idle_consider 合法中间态当日落残留)
- C2  版本盲暖池(A 类变更后旧版暖 Pod 被 min_idle 底数永久保护)
- C3  健康探测用 scope 当前配置探存量 Pod(A 类变更 20s 杀老 Pod)
- C4  deploy 失败/取消在 K8s 留孤儿物理 Pod(无 K8s→Redis 对账)
- C5  REGISTER 步失败泄漏 deploying 占位(register 在占位清理保护之外)
- C6  死 Pod 幂等回放复活(idem 续期 + REGISTER_POD 无存活校验)
- C7  等待者丢唤醒窗口(宣称的 ≤500ms 安全轮询未实现)
- C8  route refresh 无限自旋(notify_pod_dead 窗口内新落的会话永久热循环)
- C9  waiters 无 deadline 自清(崩溃副本遗留名额永久占用)
- C10 config_sync 校验缺口(int 畸形 500 / 0 值策略字段放行)
- C11 被删 scope 的 min_idle=0 推送失败后永不收敛(幻影 scope 烧容量)
- C12 同载荷重试跳过日落软摘除(部分失败后旧版 Pod 继续接新流量)
- C13 sse_path 缺前导 / 时拼出非法 URL
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.errors import (
    DeployFailed,
    InvalidParams,
    NoPodAvailable,
    ScopeFullTimeout,
)
from agent_runtime.resource_manager.k8s import FakeK8sPodClient
from agent_runtime.resource_manager.models import POD_LABEL_SELECTOR
from agent_runtime.session_manager.config_store import ConfigStore
from agent_runtime.util import now_ts
from tests.conftest import Runtime, requires_lua

SCOPE = "scope-main"


# -------------------------------------------------------------- 载荷与流程助手

def _tpl(template_id: str = "tpl-1", **overrides) -> dict:
    base = {
        "agent_image": "agentserver:1.0",
        "namespace": "default",
        "scope_concurrency": 3,
        "pod_concurrency": 2,
        "session_ttl": 60,
        "pod_ttl": 300,
        "min_idle_pods": 0,
    }
    base.update(overrides)
    return {"template_id": template_id, **base}


def _sync_payload(template: dict | None = None, scope_ids=(SCOPE,),
                  template_ids=None) -> dict:
    """全量下发载荷(三段式):每个 scope 一个同构条目(空 routing_rules = 通配兜底)。"""
    from tests.conftest import split_sync_payload

    template = template or _tpl()
    tids = list(template_ids) if template_ids is not None else [template["template_id"]]
    return split_sync_payload(
        [template],
        [
            {"scope_id": sid, "index": i, "template_id": tids[i], "routing_rules": ""}
            for i, sid in enumerate(scope_ids)
        ],
    )


async def _natural_idle(runtime: Runtime, session: str = "sess_1",
                        ttl_wait: float = 1.6) -> str:
    """等会话自然到期 → 驱动真实 sweep tick → 等 idle_consider 落 RM idle 池。

    返回转入 idle 的 pod_id(真实生命周期:候选 ZREM、registered 保留、
    RM idle SADD + idle_since 起计时——全程零手工键操作)。
    """
    await asyncio.sleep(ttl_wait)
    await runtime.sm_sweeper.sweep_once()
    for _ in range(100):
        idle = await runtime.rm_state.idle_pods(SCOPE)
        if idle:
            return idle[0]
        await asyncio.sleep(0.02)
    raise AssertionError("pod never transitioned to idle via real lifecycle")


# -------------------------------------------------------------- C1:409 日落误判

@requires_lua
async def test_C1a_sync_after_natural_idle_succeeds_min_idle0(runtime):
    """C1(min_idle=0 变体):Pod 服务过流量→自然转 idle→纯 B 类变更应成功。

    「registered ∖ candidates」同时是 idle_consider 的合法中间态(HLD §5.1),
    只有真实日落遗留才该拒绝;本场景从未发生日落。
    """
    await runtime.seed_template(session_ttl=1, pod_ttl=30)
    await runtime.route("sess_1")
    pod = await _natural_idle(runtime)
    # 前置自检:合法中间态成立(不在候选、仍在注册集)
    assert pod not in await runtime.sm_state.scope_pod_ids(SCOPE)
    assert f"{SCOPE}:{pod}" in await runtime.sm_state.registered_pods()

    result = await runtime.config_store.config_sync(
        _sync_payload(_tpl(session_ttl=2))
    )
    assert result["ok"] is True, "B 类变更被误判为日落未完成(409)= C1 实锤"


@requires_lua
async def test_C1b_sync_not_blocked_forever_by_min_idle_protection(runtime):
    """C1(min_idle≥1 变体):受底数保护的 idle Pod 不该把 409 变成永久。

    场景:min_idle=1、pod_ttl=2(短);暖 Pod 服务过一个会话后回 idle;
    等 idle_since 超过 pod_ttl 并驱动真实 reclaim tick——底数保护使它不被回收
    (这是 C2 的前提);此后 B 类变更应仍可下发生效。
    """
    await runtime.seed_template(min_idle_pods=1, session_ttl=1, pod_ttl=2)
    await runtime.rm_sweeper.autoscale_once()            # 预热 1 个暖 Pod(无请求)
    await runtime.route("sess_1")                        # 复用暖 Pod → 进 SM 注册
    pod = await _natural_idle(runtime)
    await asyncio.sleep(2.5)                             # 自然老化超过 pod_ttl
    for _ in range(3):
        await runtime.rm_sweeper.reclaim_once()
    assert [pod] == await runtime.rm_state.idle_pods(SCOPE), \
        "前置失败:底数保护语义变了,C2 前提不成立"

    result = await runtime.config_store.config_sync(
        _sync_payload(_tpl(min_idle_pods=1, session_ttl=2))
    )
    assert result["ok"] is True, "受保护 idle Pod 使 config_sync 永久 409 = C1 实锤"


# -------------------------------------------------------------- C2:版本盲暖池

@requires_lua
async def test_C2_pool_renews_after_class_a_change(runtime):
    """C2:A 类变更后,不可复用的旧版暖 Pod 必须被替换/回收,新流量不得 503。

    max_pods=1(scope_cc=2/pod_cc=2):旧 v1 暖 Pod 若既不被回收(底数保护)又
    占 max_pods 槽位,则 acquire 恒 max_reached——期望行为是暖池换代。
    """
    await runtime.seed_template(min_idle_pods=1, session_ttl=60, pod_ttl=2,
                                scope_concurrency=2, pod_concurrency=2)
    await runtime.rm_sweeper.autoscale_once()            # v1 暖 Pod ×1
    assert len(await runtime.rm_state.idle_pods(SCOPE)) == 1

    await runtime.config_store.config_sync(
        _sync_payload(_tpl(agent_image="agentserver:2.0", min_idle_pods=1,
                           session_ttl=60, pod_ttl=2,
                           scope_concurrency=2, pod_concurrency=2))
    )
    # 驱动真实 tick + 自然老化超过 pod_ttl:期望池完成换代
    for _ in range(4):
        await runtime.rm_sweeper.autoscale_once()
        await runtime.rm_sweeper.reclaim_once()
        await asyncio.sleep(0.8)

    result = await runtime.route("sess_new")
    assert result["pod_id"], "旧版暖 Pod 钉死 max_pods 槽位 → 新会话 NO_POD = C2 实锤"
    assert len(runtime.k8s.deployed_specs) >= 2, \
        "从未部署新版本暖 Pod(skip_warm 被旧版满足)= C2 实锤"


# -------------------------------------------------------------- C3:探测杀老 Pod

class _VersionedProbeK8s(FakeK8sPodClient):
    """probe 按 (ip, port, path) 判定——补齐 FakeK8s 忽略探测参数的保真度缺口。

    每个 Pod 记录自己 deploy 时烘焙的 (sse_port, health_path);探测参数不匹配
    即不通(对齐真 AgentServer 契约:路径/端口错 = 404/连拒)。
    """

    def __init__(self, default_namespace: str = "default") -> None:
        super().__init__(default_namespace)
        self.probe_targets: dict[str, tuple[int, str]] = {}

    async def deploy(self, pod_spec: dict) -> object:
        info = await super().deploy(pod_spec)
        self.probe_targets[info.pod_ip] = (
            int(pod_spec.get("sse_port") or 8080),
            pod_spec.get("health_path") or "/health",
        )
        return info

    async def probe_health(self, pod_ip: str, sse_port: int,
                           health_path: str = "/health") -> bool:
        return self.probe_targets.get(pod_ip) == (int(sse_port), health_path)


@requires_lua
async def test_C3_health_path_change_spares_active_sessions(db_handler,
                                                             redis_client):
    """C3:health_path A 类变更后,带活跃会话的老版本 Pod 必须存活到自然老化。

    日落承诺(HLD 场景 M):存量会话亲和不受影响;探测参数应取 Pod 自己
    烘焙的版本,而不是 scope 当前配置。
    """
    k8s = _VersionedProbeK8s()
    runtime = Runtime(db_handler, redis_client, k8s)
    await runtime.seed_template(session_ttl=60)
    r1 = await runtime.route("sess_1")
    old_pod = r1["pod_id"]

    await runtime.config_store.config_sync(
        _sync_payload(_tpl(health_path="/api/v1/health", session_ttl=60))
    )
    await runtime.rm_sweeper.watch_once()
    await runtime.rm_sweeper.watch_once()               # 连续 2 败即判半死

    assert await runtime.rm_state.pod_count(SCOPE) == 1, \
        "老版本 Pod 被探测判死清除 = C3 实锤"
    touched = await runtime.orchestrator.touch("sess_1")
    assert touched is True, "活跃会话随老 Pod 被 evict = C3 实锤"


# -------------------------------------------------------------- C4:孤儿物理 Pod

class _HangAfterCreateK8s(FakeK8sPodClient):
    """保真取消模型:Pod 已 create,deploy 卡在等 Ready 窗口(可被 cancel)。

    与 RealK8s.deploy 同款清理契约:取消/失败先删自己建的 Pod 再上抛。
    """

    async def deploy(self, pod_spec: dict) -> object:
        info = await super().deploy(pod_spec)
        try:
            await asyncio.sleep(30)
        except BaseException:
            await self.delete(info.pod_id, info.namespace)
            raise
        return info


@requires_lua
async def test_C4a_failed_deploy_leaves_no_physical_pod(db_handler, redis_client):
    """C4a:deploy 超时失败后,物理 Pod 必须被清理(不留孤儿)。

    用 FakeK8s 的 ``fail_after_create`` 旋钮:create 成功但永不 Ready
    (DeployFailed 携带 pod_id)——上层兜底删除必须兜住。
    """
    k8s = FakeK8sPodClient()
    k8s.fail_after_create = 1
    runtime = Runtime(db_handler, redis_client, k8s)
    await runtime.seed_template(ready_timeout=1)

    with pytest.raises((NoPodAvailable, DeployFailed)):
        await runtime.route("sess_1")

    leftover = await k8s.list_pods("default", POD_LABEL_SELECTOR)
    assert leftover == [], f"失败 deploy 泄漏物理 Pod:{[p.pod_id for p in leftover]}"


@requires_lua
async def test_C4b_cancelled_deploy_leaves_no_physical_pod(db_handler, redis_client):
    """C4b:取消落在等 Ready 窗口(Pod 已建)——Redis 占位已清(⑤),物理 Pod 呢?"""
    k8s = _HangAfterCreateK8s()
    runtime = Runtime(db_handler, redis_client, k8s)
    await runtime.seed_template()

    task = asyncio.create_task(runtime.route("sess_1"))
    await asyncio.sleep(0.3)                 # Pod 已 create,卡在等待窗口
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await runtime.rm_state.deploying_count(SCOPE) == 0, \
        "Redis 占位未清(⑤修复失效)"
    leftover = await k8s.list_pods("default", POD_LABEL_SELECTOR)
    assert leftover == [], f"取消的 deploy 泄漏物理 Pod:{[p.pod_id for p in leftover]}"


# -------------------------------------------------------------- C5:REGISTER 步占位泄漏

@requires_lua
async def test_C5_register_failure_does_not_permanently_consume_slot(runtime):
    """C5:REGISTER 步失败(清理保护之外)不得永久吃掉 max_pods 容量。

    max_pods=2:一次 register 失败后,池应仍能扩到 2 个可用 Pod。
    """
    await runtime.seed_template(scope_concurrency=2, pod_concurrency=1)  # max=2
    armed = {"boom": True}
    orig_register = runtime.rm_state.register_pod

    async def flaky_register(*args, **kwargs):
        if armed["boom"]:
            armed["boom"] = False
            raise RuntimeError("redis hiccup at REGISTER step")
        return await orig_register(*args, **kwargs)

    runtime.rm_state.register_pod = flaky_register
    with pytest.raises(Exception):
        await runtime.route("sess_1")        # deploy 成功、REGISTER 失败
    runtime.rm_state.register_pod = orig_register

    await runtime.route("sess_2")            # 第 2 个 Pod 正常部署
    result = await runtime.route("sess_3")   # 期望:仍能扩到 max_pods=2
    assert result["pod_id"], "幽灵占位永久虚占 max_pods → 池容量缩水 = C5 实锤"


# -------------------------------------------------------------- C6:死 Pod 幂等回放

@requires_lua
async def test_C6_dead_pod_not_replayed_to_retrying_client(runtime):
    """C6:同 request_id 重试不得回放已 PURGE 的死 Pod(idem 命中需校验存活)。"""
    await runtime.seed_template(session_ttl=60)
    r1 = await runtime.route("sess_1", request_id="req-1")
    dead_pod = r1["pod_id"]

    runtime.k8s.dead_pods.add(dead_pod)
    await runtime.rm_sweeper.watch_once()    # 判死 → PURGE + notify(evict s1)
    assert f"{SCOPE}:{dead_pod}" not in await runtime.sm_state.registered_pods()

    r2 = await runtime.route("sess_1", request_id="req-1")   # 网关同 id 重试
    assert r2["pod_id"] != dead_pod, "死 Pod 经幂等缓存复活并回放 = C6 实锤"


# -------------------------------------------------------------- C7:等待者丢唤醒

@requires_lua
async def test_C7_lost_wake_signal_recovered_by_poll(runtime, monkeypatch):
    """C7:free 信号早于 subscribe 发布(丢失)→ 安全轮询应兜底重新仲裁。

    调度注入:让释放(evict,PUBLISH free)恰好发生在 subscribe 完成之前——
    真实并发里天然存在的窗口;实现宣称的「≤500ms 安全轮询双保险」应使
    等待者在 ~0.5s 内重新仲裁成功,而不是空等满 scope_full_timeout。
    """
    await runtime.seed_template(scope_concurrency=1, pod_concurrency=1)
    runtime.orchestrator.scope_full_timeout = 2.0
    await runtime.route("sess_1")            # 占满(1/1)

    real_pubsub = runtime.redis.pubsub

    class _PubsubProxy:
        def __init__(self, inner):
            self._inner = inner

        async def subscribe(self, channel):
            await runtime.sm_state.evict("sess_1")   # 注入:先释放(PUBLISH 丢失)
            await self._inner.subscribe(channel)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(runtime.redis, "pubsub", lambda: _PubsubProxy(real_pubsub()))
    try:
        result = await asyncio.wait_for(runtime.route("sess_2"), timeout=1.5)
        assert result["pod_id"], "丢信号后无兜底仲裁,等待者空等至超时 = C7 实锤"
    finally:
        monkeypatch.undo()


# -------------------------------------------------------------- C8:refresh 自旋

@requires_lua
def test_C8_refresh_survives_pod_info_cleanup():
    """C8:会话亲和的 Pod 被清(info 没了)→ 再 route 必须换绑,不得无限自旋。

    按 notify_pod_dead 的真实顺序驱动窗口:evict 已枚举会话 → 窗口内新会话
    落上该 Pod → cleanup_pod 收口。此后该会话再 route 应快速换绑。

    线程隔离的原因:refresh 热循环的两个 await 在 fakeredis 上同步完成、无
    真实挂起点,同循环内 wait_for 的超时回调会被饿死(生产真 Redis 下由框架
    请求超时兜底)——用独立线程 + 硬 join 模拟「请求必须有界完成」。
    """
    import tempfile
    import threading

    box: dict = {}

    def _worker() -> None:
        async def _scenario():
            import os

            from fakeredis.aioredis import FakeRedis
            from openjiuwen_runtime.foundation.db import SQLiteHandler

            from agent_runtime.resource_manager.k8s import FakeK8sPodClient
            from agent_runtime.session_manager.config_store import (
                ROUTING_SCOPE_TABLE_DEF,
                SERVICE_CONFIG_CONTAINER_TABLE_DEF,
                SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
            )
            from tests.conftest import Runtime

            db = SQLiteHandler(os.path.join(tempfile.mkdtemp(), "c8.db"))
            await db.connect()
            await db.init_table(SERVICE_CONFIG_TEMPLATE_TABLE_DEF)
            await db.init_table(SERVICE_CONFIG_CONTAINER_TABLE_DEF)
            await db.init_table(ROUTING_SCOPE_TABLE_DEF)
            redis = FakeRedis()
            runtime = Runtime(db, redis, FakeK8sPodClient())
            try:
                await runtime.seed_template(session_ttl=60)
                r1 = await runtime.route("sess_1")
                doomed = r1["pod_id"]
                await runtime.sm_state.evict("sess_1")             # 释放容量
                await runtime.route("sess_2")                      # 新会话落上 P
                await runtime.sm_state.cleanup_pod(SCOPE, doomed)  # 窗口收口
                box["result"] = await asyncio.wait_for(
                    runtime.route("sess_2"), timeout=5.0
                )
            finally:
                await redis.flushall()
                await redis.aclose()
                await db.disconnect()

        try:
            asyncio.run(_scenario())
        except BaseException as exc:  # noqa: BLE001 - 记录线程内异常
            box["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=15)
    assert not thread.is_alive(), \
        "refresh 分支无限热循环:连 5s 硬超时都无法终止(事件循环被饿死)= C8 实锤"
    assert box.get("result"), f"路由未正常返回:{box}"


# -------------------------------------------------------------- C9:waiters 无自清

@requires_lua
async def test_C9_crashed_waiter_ghosts_do_not_hijack_queue(runtime):
    """C9:崩溃副本遗留的 waiter 名额应可自清(对照 deploy_followers 的 deadline)。

    幽灵经真实闸门入队(LUA_WAITER_GATE),此后无任何清理路径——新请求应能
    正常入队等待(504),而不是被永久 503 SCOPE_QUEUE_FULL。
    """
    await runtime.seed_template(scope_concurrency=1, pod_concurrency=1)
    await runtime.route("sess_1")            # 占满
    # 幽灵经真实闸门入队,deadline 已过期(崩溃副本遗留 1 个等待周期以上)
    assert await runtime.sm_state.try_add_waiter(SCOPE, "ghost-1", 2, now_ts() - 10)
    assert await runtime.sm_state.try_add_waiter(SCOPE, "ghost-2", 2, now_ts() - 10)
    runtime.orchestrator.scope_full_timeout = 0.5

    with pytest.raises(ScopeFullTimeout):    # 期望:能入队、等满超时(504)
        await runtime.route("sess_2", request_id="req-live")


# -------------------------------------------------------------- C10:校验缺口

@requires_lua
async def test_C10a_malformed_int_field_rejected_as_400(runtime):
    """C10a:模板 int 字段畸形("abc")必须 400,不得 500(ValueError 裸抛)。"""
    await runtime.seed_template()
    with pytest.raises(InvalidParams):
        await runtime.config_store.config_sync(_sync_payload(_tpl(session_ttl="abc")))


@requires_lua
async def test_C10b_zero_pod_concurrency_rejected(runtime):
    """C10b:pod_concurrency=0 是拒绝服务配置(满 max_pods 个必用不上的 Pod)。"""
    await runtime.seed_template()
    with pytest.raises(InvalidParams):
        await runtime.config_store.config_sync(
            _sync_payload(_tpl(pod_concurrency=0))
        )


# -------------------------------------------------------------- C11:幻影 scope

@requires_lua
async def test_C11_deleted_scope_drain_converges_after_push_failure(runtime):
    """C11:被删 scope 的 min_idle=0 推送失败后,后续 sync 必须补推(自然排空)。

    注入:drain 推送(pool 无 spec 且 min_idle=0)失败一次,模拟滚动重启时
    扩散③中断;此后同载荷再 sync 不含该 scope(old_scopes 已无它)。
    """
    from tests.conftest import split_sync_payload

    two_scopes = split_sync_payload(
        [_tpl(), _tpl("tpl-aux", min_idle_pods=1)],
        [
            {"scope_id": SCOPE, "index": 0, "template_id": "tpl-1",
             "routing_rules": ""},
            {"scope_id": "scope-aux", "index": 1, "template_id": "tpl-aux",
             "routing_rules": ""},
        ],
    )
    await runtime.config_store.config_sync(two_scopes)

    orig_push = runtime.config_store._push

    async def flaky_drain(scope_id, pool, pod_spec):
        if pool.get("min_idle_pods") == 0 and pod_spec is None:
            runtime.config_store._push = orig_push     # 只失败一次
            raise RuntimeError("drain push lost (rolling restart)")
        await orig_push(scope_id, pool, pod_spec)

    runtime.config_store._push = flaky_drain
    await runtime.config_store.config_sync(_sync_payload())   # 删除 scope-aux

    # 后续 sync(同载荷)——期望补推 drain 使幻影排空
    for _ in range(2):
        await runtime.config_store.config_sync(_sync_payload())
        await runtime.rm_sweeper.autoscale_once()
        await asyncio.sleep(0.05)

    assert await runtime.rm_state.pod_count("scope-aux") == 0, \
        "幻影 scope 永久保活(min_idle=0 推送丢失后无收敛路径)= C11 实锤"


# -------------------------------------------------------------- C12:重试跳过日落

@requires_lua
async def test_C12_sunset_retry_removes_stale_version_pod(runtime, monkeypatch):
    """C12:日落步中途失败后,同载荷重试必须补跑软摘除(旧版 Pod 不接新流量)。"""
    await runtime.seed_template(session_ttl=60)
    r1 = await runtime.route("sess_1")
    old_pod = r1["pod_id"]

    calls = {"n": 0}
    orig = ConfigStore._soft_remove_stale_pods

    async def die_once(self, scope_id, new_ver):
        if calls["n"] == 0:
            calls["n"] = 1
            raise RuntimeError("crash between DB write and sunset sweep")
        return await orig(self, scope_id, new_ver)

    monkeypatch.setattr(ConfigStore, "_soft_remove_stale_pods", die_once)
    a_change = _tpl(agent_image="agentserver:2.0", session_ttl=60)
    with pytest.raises(RuntimeError):
        await runtime.config_store.config_sync(_sync_payload(a_change))
    retry = await runtime.config_store.config_sync(_sync_payload(a_change))
    assert retry["ok"] is True

    r2 = await runtime.route("sess_2")      # pod_cc=2:旧 Pod 若仍在候选必被选中
    assert r2["pod_id"] != old_pod, \
        "重试跳过软摘除,旧版本 Pod 继续接新流量 = C12 实锤"


# -------------------------------------------------------------- C13:URL 拼接

@requires_lua
async def test_C13_sse_path_without_leading_slash_forms_valid_url(runtime):
    """C13:sse_path 缺前导 /(下发常见手误)不得拼出非法 URL。"""
    from urllib.parse import urlsplit

    try:
        await runtime.seed_template(sse_path="api/v1/stream")
    except InvalidParams:
        return                              # 下发侧已校验 = 该问题不存在(证伪)
    r1 = await runtime.route("sess_1")
    parts = urlsplit(r1["pod_sse_url"])
    assert parts.port == 8080, \
        f"端口段非法(粘连路径):{r1['pod_sse_url']!r} = C13 实锤"
    assert parts.path == "/api/v1/stream", \
        f"路径未归一:{r1['pod_sse_url']!r} = C13 实锤"
