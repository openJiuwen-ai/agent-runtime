# coding: utf-8
"""Config 层测试（scope 重构版）：全量 config_sync / resolve 快照匹配 /
eager 预热推送 / A-B 类扩散 / 409 / 红线（场景 M）。"""

from __future__ import annotations

import pytest

from agent_runtime.errors import ConfigNotFound, ConfigSyncBusy, InvalidParams
from tests.conftest import requires_lua

SCOPE = "scope-main"


def _payload(templates: list[dict], scopes: list[dict]) -> dict:
    return {"templates": templates, "scopes": scopes}


def _tpl(template_id: str, **overrides) -> dict:
    return {"template_id": template_id, "agent_image": "agentserver:1.0",
            **overrides}


def _scope(scope_id: str, template_id: str, index: int = 0,
           rules: list | None = None) -> dict:
    return {"scope_id": scope_id, "index": index,
            "template_id": template_id, "routing_rules": rules or []}


# -------------------------------------------------------------- resolve / 快照

@requires_lua
async def test_resolve_matches_rule_and_returns_scope_id(runtime):
    """规则命中的 scope 返回 (scope_id, Template);不再产生 per-scope 缓存键。"""
    await runtime.config_store.config_sync(_payload(
        [_tpl("tpl-vip"), _tpl("tpl-fb")],
        [
            _scope("vip", "tpl-vip", index=0, rules=[
                {"expressions": [{"field": "group_id", "op": "in",
                                  "values": ["ga"]}]},
            ]),
            _scope("fallback", "tpl-fb", index=100),
        ],
    ))
    scope_id, template = await runtime.config_store.resolve("u1", "ga", "bot")
    assert scope_id == "vip" and template.template_id == "tpl-vip"
    # 通配兜底
    scope_id, template = await runtime.config_store.resolve("u1", "other", "bot")
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
        [_scope("scoped", "tpl-1", rules=[
            {"expressions": [{"field": "bot_id", "op": "in", "values": ["b1"]}]},
        ])],
    ))
    with pytest.raises(ConfigNotFound):
        await runtime.config_store.resolve("u1", "g", "b-other")


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
            _scope("precise", "tpl-b", index=10, rules=[
                {"expressions": [{"field": "user_id", "op": "in",
                                  "values": ["u-admin"]}]},
            ]),
            _scope("broad", "tpl-a", index=1, rules=[
                {"expressions": [{"field": "group_id", "op": "in",
                                  "values": ["ga", "gb"]}]},
            ]),
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


@requires_lua
async def test_config_sync_missing_wildcard_warns_but_applies(runtime, caplog):
    """缺通配 scope → 仅 WARNING 放行,响应 wildcard_present=False。"""
    with caplog.at_level("WARNING", logger="agent_runtime.session_manager"):
        result = await runtime.config_store.config_sync(_payload(
            [_tpl("t")],
            [_scope("s", "t", rules=[
                {"expressions": [{"field": "group_id", "op": "in",
                                  "values": ["g"]}]},
            ])],
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
            {"templates": [_tpl("x")], "scopes": [_scope("s", "x")]}
        )


@requires_lua
async def test_config_sync_rejects_when_sunset_pending(runtime):
    """日落中间态:受影响 scope 有待回收 Pod → 409,且 DB 未被改动(检查先于写库)。"""
    await runtime.seed_template()
    await runtime.route("sess_1")
    # 手工制造中间态:Pod 在 pods:registered 但不在 scope:pods(已软摘除待回收)
    await runtime.sm_state.redis.zrem(
        runtime.sm_state.k.scope_pods(SCOPE),
        (await runtime.sm_state.registered_pods())[0].split(":", 1)[1],
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
