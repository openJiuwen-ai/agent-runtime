# coding: utf-8
"""Config 层测试（M3）：resolve 匹配 / config_sync op / diff A-B 类 / 串行化（场景 M）。"""

from __future__ import annotations

import pytest

from agent_runtime.errors import ConfigNotFound, ConfigSyncBusy, InvalidParams
from agent_runtime.util import scope_id_of
from tests.conftest import requires_lua

GRP_BOT = ("grp", "bot")
SCOPE = scope_id_of(*GRP_BOT)


@requires_lua
async def test_resolve_matches_exact_over_wildcard(runtime):
    """精确 (group,bot) 优先于 * 兜底。"""
    await runtime.seed_template(template_id="tpl-wild")
    await runtime.config_store.config_sync(
        {"kind": "template", "op": "create", "template_id": "tpl-exact",
         "template": {"agent_image": "agentserver:2.0", "session_ttl": 120}}
    )
    await runtime.config_store.config_sync(
        {"kind": "routing_rule", "op": "create", "rule_id": "rule-exact",
         "group_id": "grp", "bot_id": "bot", "template_id": "tpl-exact"}
    )
    template = await runtime.config_store.resolve(SCOPE, *GRP_BOT)
    assert template.template_id == "tpl-exact"
    # 通配 scope 兜底
    other = await runtime.config_store.resolve(
        scope_id_of("other", "bot"), "other", "bot"
    )
    assert other.template_id == "tpl-wild"


@requires_lua
async def test_resolve_caches_in_scope_config(runtime):
    await runtime.seed_template()
    await runtime.config_store.resolve(SCOPE, *GRP_BOT)
    cached = await runtime.sm_state.redis.hgetall(
        runtime.sm_state.k.scope_config(SCOPE)
    )
    assert b"template_id" in cached and b"max_pods" in cached


@requires_lua
async def test_resolve_no_match_raises_config_not_found(runtime):
    with pytest.raises(ConfigNotFound):
        await runtime.config_store.resolve(SCOPE, *GRP_BOT)


@requires_lua
async def test_config_sync_template_create_update_delete(runtime):
    store = runtime.config_store
    await store.config_sync(
        {"kind": "template", "op": "create", "template_id": "t1",
         "template": {"agent_image": "img:1"}}
    )
    assert (await store.get_template("t1")).agent_image == "img:1"
    result = await store.config_sync(
        {"kind": "template", "op": "update", "template_id": "t1",
         "updates": {"agent_image": "img:2", "session_ttl": 90}}
    )
    assert result["ok"] is True
    updated = await store.get_template("t1")
    assert updated.agent_image == "img:2" and updated.session_ttl == 90
    result = await store.config_sync(
        {"kind": "template", "op": "delete", "template_id": "t1"}
    )
    assert result["deleted"] == 1
    assert await store.get_template("t1") is None


@requires_lua
async def test_config_sync_template_sync_full_replace(runtime):
    store = runtime.config_store
    await store.config_sync(
        {"kind": "template", "op": "sync", "templates": [
            {"template_id": "a", "template": {"agent_image": "img:a"}},
            {"template_id": "b", "template": {"agent_image": "img:b"}},
        ]}
    )
    await store.config_sync(
        {"kind": "template", "op": "sync", "templates": [
            {"template_id": "b", "template": {"agent_image": "img:b2"}},
        ]}
    )
    assert await store.get_template("a") is None           # 不在数组 → 删
    assert (await store.get_template("b")).agent_image == "img:b2"


@requires_lua
async def test_config_sync_b_class_invalidates_cache_only(runtime):
    """B 类（策略参数）：DEL scope:config，不软摘除 Pod；推新池参数给 RM。"""
    await runtime.seed_template()
    await runtime.route("sess_1")                          # 建 scope:config + Pod
    pod_ids = await runtime.sm_state.scope_pod_ids(SCOPE)
    assert pod_ids

    runtime.pool_pushes.clear()
    await runtime.config_store.config_sync(
        {"kind": "template", "op": "update", "template_id": "tpl-1",
         "updates": {"session_ttl": 120}}
    )
    # 缓存已失效（下次 route 重新 resolve）
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.scope_config(SCOPE)
    ) == 0
    # Pod 未被软摘除（继续接新流量）
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == pod_ids
    # 推了新池参数（无 pod_spec —— B 类不附带 deploy 字段）
    assert runtime.pool_pushes and runtime.pool_pushes[-1][2] is None


@requires_lua
async def test_config_sync_a_class_sunsets_old_pods(runtime):
    """A 类（镜像变更）：deploy_ver 变 → 软摘除老版本 Pod + 推 pod_spec 给 RM。"""
    await runtime.seed_template(agent_image="agentserver:1.0")
    await runtime.route("sess_1")
    pod_ids = await runtime.sm_state.scope_pod_ids(SCOPE)
    assert pod_ids

    runtime.pool_pushes.clear()
    await runtime.config_store.config_sync(
        {"kind": "template", "op": "update", "template_id": "tpl-1",
         "updates": {"agent_image": "agentserver:2.0"}}
    )
    # 老版本 Pod 被软摘除出候选集（不再接新 session；存量会话不受影响）
    assert await runtime.sm_state.scope_pod_ids(SCOPE) == []
    # 会话亲和绑定仍在（存量粘老 Pod 直至老化）
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.session("sess_1")
    )
    # 推送附带新 deploy 字段
    assert runtime.pool_pushes and runtime.pool_pushes[-1][2] is not None
    # RM 已收到新 deploy_ver（acquire 版本过滤依据）
    cfg = await runtime.rm_state.load_scope_config(SCOPE)
    assert cfg.get("deploy_ver")
    # 下次 route → 扩新镜像 Pod
    result = await runtime.route("sess_2")
    new_ver = await runtime.sm_state.pod_deploy_ver(SCOPE, result["pod_id"])
    assert new_ver == cfg["deploy_ver"]


@requires_lua
async def test_config_sync_busy_rejects_concurrent(runtime):
    """串行化：锁被占 → 409 CONFIG_SYNC_BUSY。"""
    await runtime.sm_state.redis.set(
        runtime.sm_state.k.lock_config_sync(), "held-by-other", ex=30
    )
    with pytest.raises(ConfigSyncBusy):
        await runtime.config_store.config_sync(
            {"kind": "template", "op": "create", "template_id": "x",
             "template": {}}
        )


@requires_lua
async def test_config_sync_rejects_when_sunset_pending(runtime):
    """完成判定：受影响 scope 仍有「已日落待回收」Pod → 拒绝下一次（409）。"""
    await runtime.seed_template()
    await runtime.route("sess_1")
    # 手工制造中间态：Pod 在 pods:registered 但不在 scope:pods（已软摘除待回收）
    await runtime.sm_state.redis.zrem(
        runtime.sm_state.k.scope_pods(SCOPE),
        (await runtime.sm_state.registered_pods())[0].split(":", 1)[1],
    )
    with pytest.raises(ConfigSyncBusy):
        await runtime.config_store.config_sync(
            {"kind": "template", "op": "update", "template_id": "tpl-1",
             "updates": {"session_ttl": 99}}
        )


@requires_lua
async def test_config_sync_validation_errors(runtime):
    with pytest.raises(InvalidParams):
        await runtime.config_store.config_sync({"kind": "nope", "op": "create"})
    with pytest.raises(InvalidParams):
        await runtime.config_store.config_sync({"kind": "template", "op": "wat"})
    with pytest.raises(InvalidParams):
        await runtime.config_store.config_sync(
            {"kind": "template", "op": "update", "updates": {}}
        )


@requires_lua
async def test_routing_rule_update_invalidates_all_caches(runtime):
    """规则变更无法定位受影响 scope → 全量失效缓存（resolve 便宜）。"""
    await runtime.seed_template(template_id="tpl-a")
    await runtime.route("sess_1")
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.scope_config(SCOPE)
    )
    await runtime.config_store.config_sync(
        {"kind": "routing_rule", "op": "update", "rule_id": "rule-all",
         "updates": {"template_id": "tpl-a"}}
    )
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.scope_config(SCOPE)
    ) == 0


@requires_lua
async def test_db_write_failure_skips_cache_ops(runtime, db_handler, monkeypatch):
    """红线：写 DB 失败 → 立即中止，不得 DEL 缓存 / 推送。"""
    await runtime.seed_template()
    await runtime.route("sess_1")
    runtime.pool_pushes.clear()

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_handler, "update", _boom)
    with pytest.raises(RuntimeError):
        await runtime.config_store.config_sync(
            {"kind": "template", "op": "update", "template_id": "tpl-1",
             "updates": {"session_ttl": 200}}
        )
    assert await runtime.sm_state.redis.exists(
        runtime.sm_state.k.scope_config(SCOPE)
    ) == 1          # 缓存未被动
    assert runtime.pool_pushes == []     # 未推送
