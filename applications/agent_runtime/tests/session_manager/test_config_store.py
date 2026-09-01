# coding: utf-8
"""Config 层测试（scope 重构版）：全量 config_sync / resolve 快照匹配 /
eager 预热推送 / A-B 类扩散 / 409 / 红线（场景 M）。"""

from __future__ import annotations

from datetime import datetime

import pytest

from agent_runtime.errors import ConfigNotFound, ConfigSyncBusy, InvalidParams
from tests.conftest import requires_lua, split_sync_payload

SCOPE = "scope-main"


def _payload(templates: list[dict], scopes: list[dict]) -> dict:
    """legacy 内联模板构造 → 三段式载荷(wire 独占;转换器见 conftest)。"""
    return split_sync_payload(templates, scopes)


def _tpl(template_id: str, **overrides) -> dict:
    return {"template_id": template_id, "agent_image": "agentserver:1.0",
            **overrides}


def _scope(scope_id: str, template_id: str, index: int = 0,
           expr: str | None = None) -> dict:
    return {"scope_id": scope_id, "index": index,
            "template_id": template_id, "routing_rules": expr or ""}


# -------------------------------------------------------------- resolve / 快照

@requires_lua
async def test_resolve_matches_rule_and_returns_scope_id(runtime):
    """表达式命中的 scope 返回 (scope_id, Template);不再产生 per-scope 缓存键。

    表达式含 and/or/括号/not in 任意组合(新 wire 格式的核心语义)。
    """
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-vip"), _tpl("tpl-fb")],
        [
            _scope("vip", "tpl-vip", index=0, expr=(
                "user_id in ('u1') or "
                "(group_id in ('ga') and bot_id not in ('banned'))"
            )),
            _scope("fallback", "tpl-fb", index=100),
        ],
    ))
    scope_id, template = await runtime.config_store.resolve("u1", "other", "bot")
    assert scope_id == "vip" and template.template_id == "tpl-vip"     # or 左支
    scope_id, template = await runtime.config_store.resolve("u2", "ga", "bot")
    assert scope_id == "vip"                                          # or 右支(and)
    # 通配兜底:and 右支 bot 被排除且左支不命中
    scope_id, template = await runtime.config_store.resolve("u2", "ga", "banned")
    assert scope_id == "fallback" and template.template_id == "tpl-fb"
    # 旧 per-scope resolve 缓存键不复存在
    keys = await runtime.sm_state.redis.keys(
        f"{runtime.sm_state.prefix}scope:*:config")
    assert keys == []


@requires_lua
async def test_resolve_no_match_raises_config_not_found(runtime):
    """无通配 scope 且规则不命中 → ConfigNotFound(503)。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1")],
        [_scope("scoped", "tpl-1", expr="bot_id in ('b1')")],
    ))
    with pytest.raises(ConfigNotFound):
        await runtime.config_store.resolve("u1", "g", "b-other")


def test_scope_row_with_braces_skipped(caplog):
    """手改 DB 造出含 {/} 的 scope_id(写路径有白名单挡不住直改)→ 行按损坏
    跳过并告警——{/} 会截断键前缀的 hash tag,破坏 Redis Cluster 同槽性。"""
    from agent_runtime.session_manager.config_store import _scope_from_row

    class _Row:  # 手改 DB 造出的坏行
        scope_id = "bad}scope"
        match_index = 1
        template_id = "tpl-1"
        routing_rules = ""

    with caplog.at_level("WARNING"):
        assert _scope_from_row(_Row()) is None
    assert any(
        "row corrupt" in rec.message and "bad}scope" in rec.message
        for rec in caplog.records
    )


@requires_lua
async def test_resolve_rebuilds_snapshot_after_flush(runtime):
    """快照被清(冷启动/FLUSH)→ 首次 resolve 从 DB 重建,结果一致。"""
    await runtime.seed_template()
    first = await runtime.config_store.resolve("u1", "grp", "bot")
    await runtime.sm_state.redis.delete(runtime.sm_state.k.routing_snapshot())
    runtime.config_store._snapshot = None   # 同时清进程内 memo
    second = await runtime.config_store.resolve("u1", "grp", "bot")
    assert first[0] == second[0] == SCOPE
    assert await runtime.sm_state.routing_snapshot_raw()


@requires_lua
async def test_index_order_first_fit(runtime):
    """index 从小到大 first-fit:前面的通配 scope 恒先命中。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-a"), _tpl("tpl-b")],
        [
            _scope("precise", "tpl-b", index=10, expr="user_id in ('u-admin')"),
            _scope("broad", "tpl-a", index=1, expr="group_id in ('ga', 'gb')"),
        ],
    ))
    # u-admin 命中 precise?不——broad(index=1)在前且 g∈{ga,gb} 先命中
    scope_id, _ = await runtime.config_store.resolve("u-admin", "ga", "bot")
    assert scope_id == "broad"
    # ga 之外的 group 落到 precise(index=10)
    scope_id, _ = await runtime.config_store.resolve("u-admin", "gx", "bot")
    assert scope_id == "precise"


# -------------------------------------------------------------- config_sync 落库 / 替换

@requires_lua
async def test_config_sync_full_replace_and_validation(runtime):
    store = runtime.config_store
    await store.config_sync(_payload(
        [_tpl("a"), _tpl("b")],
        [_scope("s-a", "a"), _scope("s-b", "b")],
    ))
    scopes = {s.scope_id: s for s in await store.list_scopes()}
    assert set(scopes) == {"s-a", "s-b"}
    # 全量替换:消失的 template/scope 被删,留存的更新
    result = await store.config_sync(_payload(
        [_tpl("b", agent_image="img:b2")],
        [_scope("s-b", "b", index=7)],
    ))
    assert result["ok"] is True
    assert result["templates_deleted"] == 1 and result["scopes_deleted"] == 1
    assert await store.get_template("a") is None
    assert (await store.get_template("b")).agent_image == "img:b2"
    remaining = {s.scope_id: s for s in await store.list_scopes()}
    assert set(remaining) == {"s-b"} and remaining["s-b"].index == 7


@requires_lua
async def test_config_sync_rejects_legacy_payload(runtime):
    with pytest.raises(InvalidParams):
        await runtime.config_store.config_sync(
            {"kind": "template", "op": "create", "template_id": "x"})
    with pytest.raises(InvalidParams):
        await runtime.config_store.config_sync(
            {"kind": "routing_rule", "op": "sync", "rules": []})


@requires_lua
async def test_config_sync_validation_errors(runtime):
    store = runtime.config_store
    with pytest.raises(InvalidParams):   # templates/scopes 必须是 list
        await store.config_sync({"templates": {}, "scopes": []})
    with pytest.raises(InvalidParams):   # 模板缺 template_id
        await store.config_sync({"templates": [{"agent_image": "i"}], "scopes": []})
    with pytest.raises(InvalidParams):   # scope 引用未知模板
        await store.config_sync(_payload([_tpl("t")], [_scope("s", "nope")]))
    with pytest.raises(InvalidParams):   # scope_id 含 ':'(Redis 键非法)
        await store.config_sync(_payload([_tpl("t")], [_scope("a:b", "t")]))
    with pytest.raises(InvalidParams):   # 重复 scope_id
        await store.config_sync(_payload(
            [_tpl("t")], [_scope("s", "t"), _scope("s", "t")]))
    with pytest.raises(InvalidParams):   # 表达式语法错误(锁外校验,DB/Redis 未动)
        await store.config_sync(_payload(
            [_tpl("t")], [_scope("s", "t", expr="user_id in ('a' and")]))
    with pytest.raises(InvalidParams):   # 旧结构化格式(list)已废弃
        await store.config_sync(_payload(
            [_tpl("t")], [{"scope_id": "s", "index": 0, "template_id": "t",
                           "routing_rules": [{"expressions": []}]}]))


@requires_lua
async def test_config_sync_missing_wildcard_warns_but_applies(runtime, caplog):
    """缺通配 scope → 仅 WARNING 放行,响应 wildcard_present=False。"""
    with caplog.at_level("WARNING", logger="agent_runtime.session_manager"):
        result = await runtime.config_store.config_sync(_payload(
            [_tpl("t")],
            [_scope("s", "t", expr="group_id in ('g')")],
        ))
    assert result["ok"] is True and result["wildcard_present"] is False
    assert any("NO wildcard scope" in r.message for r in caplog.records)


@requires_lua
async def test_config_sync_idempotent_replay(runtime):
    """同载荷重放:affected_scopes 为空,行为不变。"""
    await runtime.seed_template()
    result = await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1", agent_image="agentserver:1.0")],
        [_scope(SCOPE, "tpl-1")],
    ))
    assert result["affected_scopes"] == []
    scope_id, template = await runtime.config_store.resolve("u", "grp", "bot")
    assert scope_id == SCOPE and template.agent_image == "agentserver:1.0"


# -------------------------------------------------------------- eager 预热(需求核心)

@requires_lua
async def test_config_sync_warms_min_idle_without_request(runtime):
    """config_sync 后(无任何 route)autoscale 一拍即为 scope 预热 min_idle 热备。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1", min_idle_pods=1, scope_concurrency=4)],
        [_scope(SCOPE, "tpl-1")],
    ))
    # RM scope config 已被主动写入(含 pod_spec_json/deploy_ver)
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg.get("min_idle_pods") == "1" and cfg.get("pod_spec_json")
    # autoscale 一拍 → FakeK8s 长出 1 个热备 Pod 入 idle 池
    await runtime.rm_sweeper.autoscale_once()
    idle = await runtime.rm_state.idle_pods(SCOPE)
    assert len(idle) == 1


@requires_lua
async def test_config_sync_pushes_all_surviving_scopes(runtime):
    """每个存活 scope(含从未 route 的)都推池参数 + pod_spec。"""
    runtime.pool_pushes.clear()
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1"), _tpl("tpl-2")],
        [_scope("s-1", "tpl-1"), _scope("s-2", "tpl-2")],
    ))
    pushed = {sid for sid, _, _ in runtime.pool_pushes}
    assert pushed == {"s-1", "s-2"}
    assert all(spec is not None for _, _, spec in runtime.pool_pushes)


@requires_lua
async def test_deleted_scope_gets_min_idle_zero(runtime):
    """scope 从下发列表消失 → 推 min_idle=0(停预热自然排空),无强制驱逐。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1"), _tpl("tpl-2")],
        [_scope("s-keep", "tpl-1"), _scope("s-drop", "tpl-2")],
    ))
    runtime.pool_pushes.clear()
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1")],
        [_scope("s-keep", "tpl-1")],
    ))
    drop_pushes = [p for p in runtime.pool_pushes if p[0] == "s-drop"]
    assert len(drop_pushes) == 1
    pool, spec = drop_pushes[0][1], drop_pushes[0][2]
    assert pool["min_idle_pods"] == 0 and spec is None


# -------------------------------------------------------------- A/B 类扩散(沿用语义)

@requires_lua
async def test_config_sync_b_class_takes_effect_via_snapshot(runtime):
    """B 类(策略参数):快照覆盖即生效,新 route 立即用新值。"""
    await runtime.seed_template(session_ttl=60)
    _, template = await runtime.config_store.resolve("u", "grp", "bot")
    assert template.session_ttl == 60
    runtime.pool_pushes.clear()
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1", session_ttl=120)],
        [_scope(SCOPE, "tpl-1")],
    ))
    _, template = await runtime.config_store.resolve("u", "grp", "bot")
    assert template.session_ttl == 120
    # B 类也推池参数(带 pod_spec,服务 eager 预热)
    assert runtime.pool_pushes and runtime.pool_pushes[-1][2] is not None


@requires_lua
async def test_config_sync_a_class_sunsets_old_pods(runtime):
    """A 类(镜像变更):deploy_ver 变 → 软摘除老版本 Pod + 新 route 扩新版本。"""
    await runtime.seed_template(agent_image="agentserver:1.0")
    await runtime.route("sess_1")
    pod_ids = await runtime.sm_state.scope_pod_ids(SCOPE)
    assert pod_ids

    runtime.pool_pushes.clear()
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1", agent_image="agentserver:2.0")],
        [_scope(SCOPE, "tpl-1")],
    ))
    # 老版本 Pod 被软摘除出候选集(不再接新 session;存量会话不受影响)
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.session("sess_1")
    )
    # 推送附带新 deploy 字段;RM 已收到新 deploy_ver
    assert runtime.pool_pushes and runtime.pool_pushes[-1][2] is not None
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg.get("deploy_ver")
    # 下次 route → 扩新镜像 Pod
    result = await runtime.route("sess_2")
    new_ver = await runtime.sm_state.pod_deploy_ver(SCOPE, result["pod_id"])
    assert new_ver == cfg["deploy_ver"]


@requires_lua
async def test_config_sync_scope_template_switch_sunsets(runtime):
    """scope 换引用模板(两模板自身未变)且 deploy_ver 不同 → 同样按 A 类日落。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-a", agent_image="img:a"), _tpl("tpl-b", agent_image="img:b")],
        [_scope(SCOPE, "tpl-a")],
    ))
    await runtime.route("sess_1")
    assert await runtime.sm_state.scope_pod_ids(SCOPE)
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-a", agent_image="img:a"), _tpl("tpl-b", agent_image="img:b")],
        [_scope(SCOPE, "tpl-b")],
    ))
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []
    _, template = await runtime.config_store.resolve("u", "grp", "bot")
    assert template.template_id == "tpl-b"


# -------------------------------------------------------------- 锁 / 409 / 红线

@requires_lua
async def test_config_sync_busy_rejects_concurrent(runtime):
    """串行化:锁被占 → 409 CONFIG_SYNC_BUSY。"""
    await runtime.sm_state.redis.set(
        runtime.sm_state.k.lock_config_sync(), "held-by-other", ex=30
    )
    with pytest.raises(ConfigSyncBusy):
        await runtime.config_store.config_sync(
            _payload([_tpl("x")], [_scope("s", "x")])
        )


@requires_lua
async def test_config_sync_rejects_when_sunset_pending(runtime):
    """日落中间态:受影响 scope 有待回收 Pod → 409,且 DB 未被改动(检查先于写库)。

    中间态的判据是**版本**(registered∖candidates 且 deploy_ver ≠ 新版本):
    当前版本的空闲 Pod 属 idle_consider 合法中间态,不得拒绝(回归见
    tests/integration/test_audit_repro.py C1a/C1b)。此处按真实日落残留形态
    构造——软摘除一个版本不匹配的 Pod。
    """
    await runtime.seed_template()
    await runtime.route("sess_1")
    pod_id = (await runtime.sm_state.registered_pods())[0].split(":", 1)[1]
    # 真实日落残留形态:软摘除(ZREM 候选)+ pod:info 记旧版本号
    await runtime.sm_state.redis.zrem(runtime.sm_state.k.scope_pods(SCOPE), pod_id)
    await runtime.sm_state.redis.hset(
        runtime.sm_state.k.pod_info(SCOPE, pod_id), "deploy_ver", "stale-ver-x",
    )
    with pytest.raises(ConfigSyncBusy):
        await runtime.config_store.config_sync(_payload(
            [_tpl("tpl-1", session_ttl=99)],
            [_scope(SCOPE, "tpl-1")],
        ))
    # 拒绝时 DB 未动(旧值仍在)
    assert (await runtime.config_store.get_template("tpl-1")).session_ttl == 60


@requires_lua
async def test_db_write_failure_skips_snapshot_and_push(runtime, db_handler, monkeypatch):
    """红线:写 DB 失败 → 立即中止,不得 SET 快照、不得推送。"""
    await runtime.seed_template()
    snapshot_before = await runtime.sm_state.routing_snapshot_raw()
    runtime.pool_pushes.clear()

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_handler, "update", _boom)
    with pytest.raises(RuntimeError):
        await runtime.config_store.config_sync(_payload(
            [_tpl("tpl-1", session_ttl=200)],
            [_scope(SCOPE, "tpl-1")],
        ))
    assert await runtime.sm_state.routing_snapshot_raw() == snapshot_before
    assert runtime.pool_pushes == []


# -------------------------------------------------------------- sidecars(多容器)

_SIDECAR = {
    "name": "jiuwenbox",
    "image": "jiuwenbox-amd64:0.0.1",
    "port": 8321,
    "env": {"JIUWENBOX_LISTEN": "tcp://0.0.0.0:8321"},
    "privileged": True,
    "capabilities_add": ["SYS_ADMIN", "NET_ADMIN"],
    "seccomp_unconfined": True,
    "apparmor_unconfined": True,
    "host_path_mounts": [
        {"host_path": "/sys/fs/cgroup", "mount_path": "/sys/fs/cgroup"},
    ],
    "readiness_probe_type": "tcp",
}


@requires_lua
async def test_config_sync_persists_and_roundtrips_sidecars(runtime):
    """sidecars 下发 → DB(JSON 列) → get_template 回读 = 规范形(默认键填满)。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-box", sidecars=[_SIDECAR])],
        [_scope(SCOPE, "tpl-box")],
    ))
    t = await runtime.config_store.get_template("tpl-box")
    assert t.sidecars == [{
        "name": "jiuwenbox",
        "image": "jiuwenbox-amd64:0.0.1",
        "port": 8321,
        "env": {"JIUWENBOX_LISTEN": "tcp://0.0.0.0:8321"},
        "image_pull_policy": "IfNotPresent",
        "cpu_request": None, "memory_request": None,
        "cpu_limit": None, "memory_limit": None,
        "privileged": True,
        "capabilities_add": ["SYS_ADMIN", "NET_ADMIN"],
        "capabilities_drop": [],
        "seccomp_unconfined": True,
        "apparmor_unconfined": True,
        "run_as_user": None, "run_as_group": None,
        "host_path_mounts": [{"host_path": "/sys/fs/cgroup",
                              "mount_path": "/sys/fs/cgroup",
                              "read_only": False, "host_path_type": None}],
        "configmap_mounts": [], "pvc_mounts": [],
        "readiness_probe_type": "tcp",
        "readiness_path": "/health",
        "readiness_initial_delay": 5,
        "readiness_period": 10,
        "readiness_timeout_seconds": 3,
    }]
    # deploy_subset 携带 sidecars;json 可序列化(RM 缓存 pod_spec_json 用)
    subset = t.deploy_subset()
    import json
    assert subset["sidecars"] == t.sidecars
    assert json.loads(json.dumps(subset))["sidecars"][0]["name"] == "jiuwenbox"
    # pool 推送同样携带
    assert runtime.pool_pushes and runtime.pool_pushes[-1][2] is not None
    assert runtime.pool_pushes[-1][2]["sidecars"] == t.sidecars


@requires_lua
async def test_config_sync_rejects_invalid_sidecars_without_side_effect(runtime):
    """坏 sidecar 容器(拼错键)→ InvalidParams 400;锁外校验零副作用(DB 无行)。"""
    with pytest.raises(InvalidParams, match="unknown keys"):
        await runtime.config_store.config_sync({
            "containers": [
                _main_container(),
                {"container_id": "c-bad-sc", "name": "box", "image": "x:1",
                 "securityContext": {"capabilitesAdd": ["SYS_ADMIN"]}},  # 拼错键
            ],
            "templates": [_split_tpl("tpl-bad", sidecars=["c-bad-sc"])],
            "scopes": [_scope(SCOPE, "tpl-bad")],
        })
    assert await runtime.config_store.get_template("tpl-bad") is None


# ---------------------------------------------- node_name / run_as_user / run_as_group

@requires_lua
async def test_config_sync_persists_and_roundtrips_pod_placing_fields(runtime):
    """nodeName/runAsUser/runAsGroup 下发 → DB → get_template 回读;
    deploy_subset 携带(进 deploy_ver 指纹,A 类)。wire 为 K8s 严格 int
    (数字串不再容忍——legacy 内联时代的 _INT_FIELDS 宽容随收紧移除)。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-pin", node_name="ecs-38b3-0001",
              run_as_user=1000, run_as_group=1000)],
        [_scope(SCOPE, "tpl-pin")],
    ))
    t = await runtime.config_store.get_template("tpl-pin")
    assert (t.node_name, t.run_as_user, t.run_as_group) == (
        "ecs-38b3-0001", 1000, 1000)
    subset = t.deploy_subset()
    assert (subset["node_name"], subset["run_as_user"],
            subset["run_as_group"]) == ("ecs-38b3-0001", 1000, 1000)


@requires_lua
async def test_config_sync_rejects_malformed_pod_placing_fields(runtime):
    """run_as_user 畸形(bool/非数字串/负值)与坏 node_name → 400,零副作用。"""
    for field, bad, match in (
            ("run_as_user", True, "must be an integer"),
            ("run_as_user", "abc", "must be an integer"),
            ("run_as_user", -1, "must be an integer"),
            ("run_as_group", -2, "must be an integer"),
            ("node_name", "bad node", "must be a hostname"),
            ("node_name", "a" * 254, "must be a hostname"),
            ("node_name", 123, "must be a hostname"),
    ):
        with pytest.raises(InvalidParams, match=match):
            await runtime.config_store.config_sync(_payload(
                [_tpl("tpl-bad", **{field: bad})],
                [_scope(SCOPE, "tpl-bad")],
            ))
        assert await runtime.config_store.get_template("tpl-bad") is None


@requires_lua
async def test_config_sync_node_name_empty_string_is_unset(runtime):
    """node_name 空串与未设同义(归一 None;渲染侧 or None,指纹不漂移)。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-plain", node_name="")],
        [_scope(SCOPE, "tpl-plain")],
    ))
    t = await runtime.config_store.get_template("tpl-plain")
    assert t.node_name is None
    assert t.deploy_subset()["node_name"] is None


@requires_lua
async def test_config_sync_pod_placing_fields_are_a_class(runtime):
    """三字段变更 → deploy_ver 变化(A 类日落判据);未设时 None 不进指纹
    (存量模板 deploy_ver 不因升级漂移)。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-a", node_name="node-1")],
        [_scope(SCOPE, "tpl-a")],
    ))
    before = (await runtime.config_store.get_template("tpl-a")).deploy_ver()
    # 未设字段的两模板:与历史指纹一致(None 被 fingerprint 滤除)
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-a", node_name="node-2")],     # 换节点 = A 类变更
        [_scope(SCOPE, "tpl-a")],
    ))
    after = (await runtime.config_store.get_template("tpl-a")).deploy_ver()
    assert before != after


@requires_lua
async def test_config_sync_sidecars_change_triggers_a_class_sunset(runtime):
    """sidecar 变更(镜像升级)是 A 类:软摘除老 Pod + 新 route 扩新版本。"""
    await runtime.seed_template(sidecars=[_SIDECAR])
    await runtime.route("sess_1")
    assert await runtime.sm_state.scope_pod_ids(SCOPE)

    runtime.pool_pushes.clear()
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1", sidecars=[dict(_SIDECAR, image="jiuwenbox-amd64:0.0.2")])],
        [_scope(SCOPE, "tpl-1")],
    ))
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []  # 软摘除
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg.get("deploy_ver")
    result = await runtime.route("sess_2")
    assert await runtime.sm_state.pod_deploy_ver(SCOPE, result["pod_id"]) == \
        cfg["deploy_ver"]
    # FakeK8s 收到的新 pod_spec 带新 sidecar 镜像
    assert runtime.k8s.deployed_specs[-1]["sidecars"][0]["image"] == \
        "jiuwenbox-amd64:0.0.2"


@requires_lua
async def test_config_sync_sidecars_removal_triggers_a_class_sunset(runtime):
    """有 → 无(去掉 sidecars)同样 A 类日落(指纹回退到无 sidecar 形态)。"""
    await runtime.seed_template(sidecars=[_SIDECAR])
    await runtime.route("sess_1")
    assert await runtime.sm_state.scope_pod_ids(SCOPE)

    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1")],
        [_scope(SCOPE, "tpl-1")],
    ))
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []  # 软摘除
    result = await runtime.route("sess_2")
    assert runtime.k8s.deployed_specs[-1]["sidecars"] is None


def test_template_from_row_normalizes_sidecars():
    """DB 行兜底:sidecars None/[]/坏值 → Template.sidecars 统一 None。"""
    from types import SimpleNamespace

    from agent_runtime.session_manager.config_store import (
        _COLUMN_OF,
        template_from_row,
    )

    def _row(sidecars_value):
        base = {column: None for column in _COLUMN_OF.values()}
        base.update(agent_image="img:1", sidecars=sidecars_value)
        return SimpleNamespace(**base)

    assert template_from_row(_row(None)).sidecars is None
    assert template_from_row(_row([])).sidecars is None
    assert template_from_row(_row("garbage")).sidecars is None
    assert template_from_row(_row([{"garbage": 1}])).sidecars is None
    assert template_from_row(_row([dict(_SIDECAR)])).sidecars is not None


@requires_lua
async def test_route_and_pool_push_carry_sidecars_end_to_end(runtime):
    """端到端:seed(sidecars) → route → FakeK8s 收到的 pod_spec 含规范形 sidecars。"""
    from agent_runtime.sidecars import validate_sidecars

    await runtime.seed_template(sidecars=[_SIDECAR])
    result = await runtime.route("sess-e2e")
    assert result["pod_id"]

    canonical = validate_sidecars([_SIDECAR], container_name="agent",
                                  sse_port=8080, container_port=8080)
    # FakeK8s 录制:deploy 真正收到 sidecars
    assert runtime.k8s.deployed_specs, "FakeK8s.deployed_specs 未录制"
    assert runtime.k8s.deployed_specs[0]["sidecars"] == canonical
    # RM scope:config 缓存的 pod_spec_json 同样携带
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    import json
    cached = json.loads(cfg["pod_spec_json"])
    assert cached["sidecars"] == canonical


@requires_lua
async def test_config_sync_roundtrips_agent_and_sidecar_mounts(runtime):
    """主容器三种挂载 + sidecar cm/pvc 下发 → DB JSON 列回读 = 规范形。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-mnt",
              agent_host_path_mounts=[{"host_path": "/host/c", "mount_path": "/etc/host"}],
              agent_configmap_mounts=[{"config_map_name": "agent-cm",
                                       "mount_path": "/etc/agent/config.yaml",
                                       "sub_path": "config.yaml"}],
              agent_pvc_mounts=[{"claim_name": "agent-data", "mount_path": "/data"}],
              sidecars=[dict(_SIDECAR,
                             configmap_mounts=[{"config_map_name": "box-policy",
                                                "mount_path": "/etc/box/policy.yaml",
                                                "sub_path": "policy.yaml"}])])],
        [_scope(SCOPE, "tpl-mnt")],
    ))
    t = await runtime.config_store.get_template("tpl-mnt")
    assert t.agent_host_path_mounts == [
        {"host_path": "/host/c", "mount_path": "/etc/host",
         "read_only": False, "host_path_type": None}]
    assert t.agent_configmap_mounts[0]["sub_path"] == "config.yaml"
    assert t.agent_configmap_mounts[0]["read_only"] is True
    assert t.agent_pvc_mounts == [{"claim_name": "agent-data",
                                   "mount_path": "/data", "read_only": False}]
    assert t.sidecars[0]["configmap_mounts"][0]["config_map_name"] == "box-policy"
    # deploy_subset 携带三列表,整体 json 可序列化
    subset = t.deploy_subset()
    import json
    json.loads(json.dumps(subset))
    assert subset["agent_pvc_mounts"] == t.agent_pvc_mounts


@requires_lua
async def test_config_sync_agent_mount_change_is_a_class(runtime):
    """主容器挂载变更(A 类):软摘除老 Pod(挂载烘焙进 Pod,同 sidecars 语义)。"""
    await runtime.seed_template(agent_configmap_mounts=[
        {"config_map_name": "cm-1", "mount_path": "/cfg"}])
    await runtime.route("sess_1")
    assert await runtime.sm_state.scope_pod_ids(SCOPE)
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1", agent_configmap_mounts=[
            {"config_map_name": "cm-2", "mount_path": "/cfg"}])],
        [_scope(SCOPE, "tpl-1")],
    ))
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []


def test_template_from_row_normalizes_agent_mounts():
    """DB 行兜底:三种主容器挂载坏值 → None(同 sidecars 单点归一)。"""
    from types import SimpleNamespace

    from agent_runtime.session_manager.config_store import (
        _COLUMN_OF,
        template_from_row,
    )

    def _row(**kw):
        base = {column: None for column in _COLUMN_OF.values()}
        base.update(agent_image="img:1", **kw)
        return SimpleNamespace(**base)

    assert template_from_row(_row(agent_pvc_mounts="garbage")).agent_pvc_mounts is None
    assert template_from_row(_row(agent_pvc_mounts=[])).agent_pvc_mounts is None
    assert template_from_row(
        _row(agent_pvc_mounts=[{"claim_name": "p", "mount_path": "/v"}])
    ).agent_pvc_mounts == [{"claim_name": "p", "mount_path": "/v", "read_only": False}]


# -------------------------------------------------------------- 三段式契约(容器表拆分)

def _main_container(container_id="c-main-1", **overrides) -> dict:
    return {"container_id": container_id, "name": "agent",
            "image": "agentserver:1.0",
            "ports": [{"name": "sse", "containerPort": 8086}],
            "imagePullPolicy": "IfNotPresent", **overrides}


def _split_tpl(template_id, main_cid="c-main-1", sidecars=None, **overrides):
    return {"template_id": template_id, "main_container_id": main_cid,
            "sidecar_container_ids": sidecars or [],
            "namespace": "default", **overrides}


def _split_payload(containers, templates, scopes=None):
    return {"containers": containers, "templates": templates,
            "scopes": scopes if scopes is not None else [
                {"scope_id": "scope-main", "index": 0,
                 "template_id": templates[0]["template_id"],
                 "routing_rules": ""}]}


@requires_lua
async def test_split_contract_deploy_ver_identical_to_inline(runtime):
    """★承重:三段式水合 Template == 逐字段等价的内联构造 → deploy_ver/快照
    JSON 逐字节相等。内联腿改为直接构造 Template(wire 层已收紧,legacy
    载荷不可再下发;双路径等价性在 2026-08-31 收紧前经实测锁定)。"""
    from agent_runtime.session_manager.models import Template
    from agent_runtime.session_manager.routing import template_to_json
    from agent_runtime.sidecars import validate_sidecars

    # 三段式下发(含 sidecar + env + 资源 + 探针 + 挂载)
    containers = [
        _main_container(
            env=[{"name": "K", "value": "v"}],
            resources={"requests": {"cpu": "500m"}},
            securityContext={"runAsUser": 1000},
            readinessProbe={"httpGet": {"path": "/api/v1/health"},
                            "periodSeconds": 7},
            volumeMounts=[{"name": "cfg", "mountPath": "/etc/agent"}]),
        {"container_id": "c-box-1", "name": "jiuwenbox", "image": "box:1",
         "ports": [{"containerPort": 8321}], "securityContext": {"privileged": True},
         "volumeMounts": [{"name": "hp", "mountPath": "/m"}]},
    ]
    templates = [_split_tpl(
        "tpl-x", sidecars=["c-box-1"],
        volumes=[{"name": "cfg", "configMap": {"name": "agent-cm"}},
                 {"name": "hp", "hostPath": {"path": "/h"}}])]
    await runtime.config_store.config_sync(
        _split_payload(containers, templates))
    split_template = await runtime.config_store.get_template("tpl-x")
    assert split_template is not None

    # 逐字段等价的内联构造(等值基准)
    inline_template = Template(
        template_id="tpl-x",
        agent_image="agentserver:1.0", sse_port=8086, container_port=8086,
        agent_env={"K": "v"}, agent_cpu_request="500m", run_as_user=1000,
        health_path="/api/v1/health", readiness_period=7,
        agent_configmap_mounts=[{
            "config_map_name": "agent-cm", "mount_path": "/etc/agent",
            "sub_path": None, "items": None, "read_only": True}],
        sidecars=validate_sidecars([{
            "name": "jiuwenbox", "image": "box:1", "port": 8321,
            "privileged": True,
            "host_path_mounts": [{"host_path": "/h", "mount_path": "/m",
                                  "read_only": False, "host_path_type": None}]}],
            container_name="agent", sse_port=8086, container_port=8086),
    )
    assert split_template.deploy_ver() == inline_template.deploy_ver()
    assert template_to_json(split_template) == template_to_json(inline_template)


@requires_lua
async def test_split_sync_persists_rows_and_routes(runtime):
    """三段式落库:模板行只持引用与 volumes,容器行齐;route 正常出 sse_url。"""
    result = await runtime.config_store.config_sync(_split_payload(
        [_main_container()], [_split_tpl("tpl-s")]))
    assert result["containers_synced"] == 1
    assert result["containers_deleted"] == 0

    from agent_runtime.session_manager.config_store import (
        CONTAINER_TABLE, TEMPLATE_TABLE)
    row = await runtime.db.get(TEMPLATE_TABLE, {"template_id": "tpl-s"})
    assert row.main_container_id == "c-main-1"
    assert row.sidecar_container_ids is None          # 空列表归一 None
    assert row.volumes is None
    assert row.agent_image == ""                      # legacy 列死值
    crow = await runtime.db.get(CONTAINER_TABLE, {"container_id": "c-main-1"})
    assert crow is not None and crow.image == "agentserver:1.0"

    outcome = await runtime.route("s1")
    assert outcome["pod_sse_url"].startswith("http://")


@requires_lua
async def test_container_image_change_updates_deploy_ver(runtime):
    """容器镜像变更 → A 类(deploy_ver 变化 + 新 pod_spec 推送)。"""
    await runtime.config_store.config_sync(_split_payload(
        [_main_container()], [_split_tpl("tpl-a")]))
    old = await runtime.config_store.get_template("tpl-a")
    await runtime.config_store.config_sync(_split_payload(
        [_main_container(image="agentserver:2.0")], [_split_tpl("tpl-a")]))
    new = await runtime.config_store.get_template("tpl-a")
    assert new.deploy_ver() != old.deploy_ver()
    assert new.agent_image == "agentserver:2.0"
    # 最后一拍推送带新 pod_spec(RM 侧 pod_spec_json/deploy_ver 收敛)
    scope_id, pool, pod_spec = runtime.pool_pushes[-1]
    assert pod_spec["agent_image"] == "agentserver:2.0"


@requires_lua
async def test_container_gc_removes_unreferenced_rows(runtime):
    """全量替换语义:本批未出现的容器行被删(containers_deleted 计数)。"""
    from agent_runtime.session_manager.config_store import CONTAINER_TABLE

    containers = [_main_container(), _main_container("c-unused")]
    # c-unused 未被引用 → 整个 payload 400(零副作用)
    with pytest.raises(InvalidParams, match=r"not referenced"):
        await runtime.config_store.config_sync(_split_payload(
            containers, [_split_tpl("tpl-g")]))

    containers = [_main_container(),
                  {"container_id": "c-box-9", "name": "box9", "image": "b:1"}]
    await runtime.config_store.config_sync(_split_payload(
        containers, [_split_tpl("tpl-g", sidecars=["c-box-9"])]))
    # 再下发去掉 sidecar 引用 → c-box-9 被 GC
    result = await runtime.config_store.config_sync(_split_payload(
        [_main_container()], [_split_tpl("tpl-g")]))
    assert result["containers_deleted"] == 1
    assert await runtime.db.get(
        CONTAINER_TABLE, {"container_id": "c-box-9"}) is None


@requires_lua
async def test_container_shared_across_templates(runtime):
    """同容器 + 同卷被多模板引用:各 hydration 正确,行唯一。"""
    containers = [
        _main_container(volumeMounts=[{"name": "hp", "mountPath": "/m"}]),
        {"container_id": "c-s", "name": "sharer", "image": "s:1"},
    ]
    volumes = [{"name": "hp", "hostPath": {"path": "/h"}}]
    templates = [
        _split_tpl("tpl-1", sidecars=["c-s"], volumes=volumes),
        _split_tpl("tpl-2", main_cid="c-main-1", sidecars=["c-s"],
                   volumes=volumes),
    ]
    await runtime.config_store.config_sync(_split_payload(containers, templates))
    for tid in ("tpl-1", "tpl-2"):
        t = await runtime.config_store.get_template(tid)
        assert t is not None
        assert t.agent_host_path_mounts == [
            {"host_path": "/h", "mount_path": "/m", "read_only": False,
             "host_path_type": None}]
        assert [sc["name"] for sc in t.sidecars] == ["sharer"]


@requires_lua
async def test_split_form_rejections_zero_side_effect(runtime):
    """legacy 载荷/mixed/悬挂引用/双角色/缺引用键 → 400 且 DB/快照零变化。"""
    from agent_runtime.session_manager.config_store import CONTAINER_TABLE

    await runtime.seed_template("tpl-base", scope_id="scope-main")
    snapshot_before = await runtime.redis.get(
        runtime.sm_state.k.routing_snapshot())
    templates_before = await runtime.db.list_records(
        "service_config_template", limit=100)
    containers_before = await runtime.db.list_records(CONTAINER_TABLE, limit=100)

    # legacy 内联载荷(wire 独占收紧后拒绝;空载荷同样要求三键)
    with pytest.raises(InvalidParams, match=r"three-part contract"):
        await runtime.config_store.config_sync(
            {"templates": [_tpl("tpl-m")], "scopes": [_scope(SCOPE, "tpl-m")]})
    with pytest.raises(InvalidParams, match=r"three-part contract"):
        await runtime.config_store.config_sync(
            {"templates": [], "scopes": []})
    with pytest.raises(InvalidParams, match=r"mixes container references"):
        await runtime.config_store.config_sync(_split_payload(
            [_main_container()],
            [{"template_id": "tpl-m", "main_container_id": "c-main-1",
              "agent_image": "leak:1"}]))
    with pytest.raises(InvalidParams, match=r"not present in the payload"):
        await runtime.config_store.config_sync(_split_payload(
            [], [_split_tpl("tpl-m", main_cid="c-ghost")]))
    with pytest.raises(InvalidParams, match=r"both main and sidecar"):
        await runtime.config_store.config_sync(_split_payload(
            [_main_container()],
            [_split_tpl("tpl-m", sidecars=["c-main-1"])]))
    # containers 段在但模板缺 main_container_id → 400
    with pytest.raises(InvalidParams, match=r"requires a non-empty main_container_id"):
        await runtime.config_store.config_sync({
            "containers": [_main_container()],
            "templates": [_tpl("tpl-m")],
            "scopes": [_scope(SCOPE, "tpl-m")]})

    assert await runtime.redis.get(
        runtime.sm_state.k.routing_snapshot()) == snapshot_before
    after = await runtime.db.list_records(
        "service_config_template", limit=100)
    assert len(after) == len(templates_before)
    containers_after = await runtime.db.list_records(CONTAINER_TABLE, limit=100)
    assert {c.container_id for c in containers_after} \
        == {c.container_id for c in containers_before}


@requires_lua
async def test_new_form_row_with_missing_container_skipped(runtime):
    """引用损坏(容器行缺失)→ 整模板跳过,get_template None,引用 scope 不命中。"""
    from agent_runtime.session_manager.config_store import (
        ROUTING_SCOPE_TABLE, TEMPLATE_TABLE)

    await runtime.seed_template("tpl-ok", scope_id="scope-ok")
    # 直插一条引用幽灵容器的新形态行(手删 DB/GC 误删形态)+ 指向它的 scope
    await runtime.db.create(TEMPLATE_TABLE, {
        "template_id": "tpl-ghost", "jiuwenclaw_id": "",
        "template_name": "", "agent_image": "", "namespace": "default",
        "pod_name": "agentserver", "container_name": "agent",
        "container_port": 8080, "port_name": "http", "sse_port": 8080,
        "sse_path": "/sse", "health_path": "/health",
        "image_pull_policy": "IfNotPresent",
        "readiness_initial_delay": 5, "readiness_period": 5,
        "ready_timeout": 300, "ready_poll_interval": 2,
        "session_concurrency": 3, "service_concurrency": 2,
        "service_ttl": 300, "session_ttl": 60, "min_idle_services": 0,
        "message_timeout": 600, "enabled": True,
        "main_container_id": "c-missing", "sidecar_container_ids": None,
        "volumes": None,
        "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
    })
    await runtime.db.create(ROUTING_SCOPE_TABLE, {
        "jiuwenclaw_id": "", "scope_id": "scope-ghost", "match_index": -1,
        "template_id": "tpl-ghost", "routing_rules": "user_id in ('ghost-u')",
        "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
    })
    await runtime.config_store.rebuild_snapshot()
    assert await runtime.config_store.get_template("tpl-ghost") is None
    # 指向幽灵模板的 scope 被跳过 → 落到通配兜底(tpl-ok 正常)。
    # 若坏模板未被跳过,快照携带空镜像模板会路由出不可部署的 Pod。
    _, template = await runtime.config_store.resolve("ghost-u", "g", "b")
    assert template.template_id == "tpl-ok"


@requires_lua
@requires_lua
async def test_legacy_payload_rejected_after_split_rows(runtime):
    """wire 独占:legacy 内联载荷 → 400,已落库的新形态行原样保留。"""
    from agent_runtime.session_manager.config_store import CONTAINER_TABLE

    await runtime.config_store.config_sync(_split_payload(
        [_main_container()], [_split_tpl("tpl-d")]))
    assert await runtime.db.get(
        CONTAINER_TABLE, {"container_id": "c-main-1"}) is not None

    with pytest.raises(InvalidParams, match=r"three-part contract"):
        await runtime.config_store.config_sync(
            {"templates": [_tpl("tpl-d", agent_image="agentserver:9.9")],
             "scopes": [_scope(SCOPE, "tpl-d")]})
    # 拒绝零副作用:容器行与模板行都未被改动
    assert await runtime.db.get(
        CONTAINER_TABLE, {"container_id": "c-main-1"}) is not None
    t = await runtime.config_store.get_template("tpl-d")
    assert t is not None and t.agent_image == "agentserver:1.0"


@requires_lua
async def test_split_template_node_name_wire_alias(runtime):
    """nodeName(K8s 拼写)→ node_name 生效;snake 双形态 → 400(防静默二义)。

    2026-08-31 真实踩点:运维脚本切三段式时 nodeName 被静默丢弃、节点绑定
    失效——K8s 拼写键必须有显式翻译与测试。
    """
    await runtime.config_store.config_sync(_split_payload(
        [_main_container()], [_split_tpl("tpl-nn", nodeName="arm-master")]))
    t = await runtime.config_store.get_template("tpl-nn")
    assert t is not None and t.node_name == "arm-master"
    with pytest.raises(InvalidParams, match=r"k8s wire spelling"):
        await runtime.config_store.config_sync(_split_payload(
            [_main_container()], [_split_tpl("tpl-nn2", node_name="arm-master")]))


# -------------------------------------------------------------- config_refresh(场景 M-R)

@requires_lua
async def test_config_refresh_sunsets_candidates_and_bumps_generation(runtime):
    """强制刷新:全 scope 候选集清空 + RM 代次 +1;配置零变化。"""
    await runtime.seed_template(agent_image="agentserver:1.0")
    await runtime.route("sess_1")
    assert await runtime.sm_state.scope_pod_ids(SCOPE)

    result = await runtime.config_store.config_refresh()
    assert result["ok"] is True
    assert result["scopes_refreshed"] == 1
    assert result["pods_sunset"] == 1
    assert result["generations"] == {SCOPE: 1}
    assert runtime.gen_bumps == [SCOPE]
    # 候选集软摘除;RM config 代次落地
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg.get("generation") == "1"
    # 配置本身未动(deploy_ver 与 seed 时一致)
    old_cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert old_cfg.get("deploy_ver")
    t = await runtime.config_store.get_template("tpl-1")
    assert t is not None and t.agent_image == "agentserver:1.0"


@requires_lua
async def test_config_refresh_preserves_session_affinity(runtime):
    """刷新后存量会话亲和不变:同 session route 回同 Pod、touch 仍 True。"""
    await runtime.seed_template()
    first = await runtime.route("sess_1")
    await runtime.config_store.config_refresh()
    # 亲和续期直读 session HASH,不查候选集 → 老 Pod 继续服务存量会话
    again = await runtime.route("sess_1")
    assert again["pod_id"] == first["pod_id"]
    assert await runtime.orchestrator.touch("sess_1") is True


@requires_lua
async def test_config_refresh_rejects_payload(runtime):
    """无载荷契约:rawdata 非空 → 400 VALIDATION,且零副作用。"""
    await runtime.seed_template()
    with pytest.raises(InvalidParams, match=r"takes no payload"):
        await runtime.config_store.config_refresh({"templates": []})
    # 零副作用:锁未被遗留、代次未动
    assert runtime.gen_bumps == []
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.lock_config_sync()) == 0
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg.get("generation") is None


@requires_lua
async def test_config_refresh_busy_conflicts_with_config_sync_lock(runtime):
    """与 config_sync 共用锁:锁被占 → 双向 409 CONFIG_SYNC_BUSY。"""
    await runtime.seed_template()
    await runtime.sm_state.redis.set(
        runtime.sm_state.k.lock_config_sync(), "held-by-other", ex=30
    )
    with pytest.raises(ConfigSyncBusy):
        await runtime.config_store.config_refresh()


@requires_lua
async def test_config_refresh_empty_db_is_noop(runtime):
    """无 scope → 空 noop 响应(不 bump 不摘除)。"""
    result = await runtime.config_store.config_refresh()
    assert result == {"ok": True, "scopes_refreshed": 0,
                      "pods_sunset": 0, "generations": {}}
    assert runtime.gen_bumps == []


@requires_lua
async def test_config_refresh_touches_neither_db_nor_snapshot(runtime):
    """刷新不写 DB 不动快照;重推池参数(值与 seed 相同,带 pod_spec)。"""
    await runtime.seed_template()
    await runtime.route("sess_1")
    snapshot_before = await runtime.sm_state.routing_snapshot_raw()
    templates_before = await runtime.config_store.list_templates()
    scopes_before = await runtime.config_store.list_scopes()

    runtime.pool_pushes.clear()
    await runtime.config_store.config_refresh()

    assert await runtime.sm_state.routing_snapshot_raw() == snapshot_before
    assert await runtime.config_store.list_templates() == templates_before
    assert await runtime.config_store.list_scopes() == scopes_before
    # 重推带 pod_spec(RM 缓存就绪,autoscale 可按存量 spec 重建)
    assert runtime.pool_pushes and runtime.pool_pushes[-1][0] == SCOPE
    assert runtime.pool_pushes[-1][2] is not None


@requires_lua
async def test_config_refresh_skips_scope_with_missing_template(runtime, caplog):
    """悬挂引用 scope(模板行被直删)→ 跳过不刷,WARNING 留痕。"""
    import logging as _logging

    await runtime.seed_template()
    # 直删模板行造悬挂引用(写路径造不出来;模板缺失时 list 水合返回 None)
    await runtime.db.delete(
        "service_config_template", {"template_id": "tpl-1"})
    with caplog.at_level(_logging.WARNING,
                         logger="agent_runtime.session_manager"):
        result = await runtime.config_store.config_refresh()
    assert result["scopes_refreshed"] == 0
    assert SCOPE not in result["generations"]
    assert runtime.gen_bumps == []
    assert any("template missing" in r.getMessage() for r in caplog.records)


@requires_lua
async def test_config_sync_not_blocked_by_refresh_sunset_pods(runtime):
    """守卫语义钉死:config_refresh 的老代 Pod(版本相同、候选集外)对
    config_sync 日落中间态守卫不可见 → 后续同版本/B 类下发不 409。

    老代 Pod 回收由 reclaim 的代次感知保证(见 test_force_refresh.py R1),
    配置面不得因刷新残留长时间 409(同 C1a/C1b 病理)。
    """
    await runtime.seed_template(agent_image="agentserver:1.0")
    await runtime.route("sess_1")
    await runtime.config_store.config_refresh()
    # 刷新残留:registered∖candidates 的老代 Pod,版本与当前配置一致
    registered = await runtime.sm_state.registered_pods()
    assert registered   # 老 Pod 仍在(会话还活着)
    # B 类下发(同 deploy_ver)不被阻塞
    result = await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-1", agent_image="agentserver:1.0", session_ttl=99)],
        [_scope(SCOPE, "tpl-1")],
    ))
    assert result["ok"] is True
