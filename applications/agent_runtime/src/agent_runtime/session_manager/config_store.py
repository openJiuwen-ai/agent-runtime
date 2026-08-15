# coding: utf-8
"""Config 层（SM 设计 §4）：template / routing_rule 持久化 + resolve + config_sync。

- 存储在共享 DB（config system-of-record），表 ``service_config_template`` /
  ``routing_rule``（列名沿用 EE 兼容名，见 models.py 模块注释）；
- ``resolve(group_id, bot_id)``：route 热路径的配置解析，Redis ``scope:config``
  缓存，miss 读 DB（精确匹配优先，``*`` 兜底）；
- ``config_sync``：Claw Manager 下发入口（场景 M）——写 DB → 逐字段 diff →
  A 类（deploy 子集变更）软摘除老版本 Pod + 推 RM；B 类 DEL 缓存；
  全程持 ``lock:config_sync`` 串行化（忙 → 409 CONFIG_SYNC_BUSY）。

红线：写 DB 失败立即中止，不得 DEL 缓存、不得推送（防 cache 脏刷新）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    TableDefinition,
)

from ..errors import ConfigNotFound, ConfigSyncBusy, InvalidParams
from ..util import now_ts, s
from .models import POLICY_FIELDS, Template
from .state import SessionState

logger = logging.getLogger("agent_runtime.session_manager")

TENANT_ID = ""  # v1 单租户；列保留为 EE 兼容

TEMPLATE_TABLE = "service_config_template"
ROUTING_RULE_TABLE = "routing_rule"

SERVICE_CONFIG_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name=TEMPLATE_TABLE,
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False, default=""),
        ColumnDefinition("template_id", "string", length=100, nullable=False, unique=True),
        ColumnDefinition("template_name", "string", length=128, nullable=False, default=""),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("agent_image", "string", length=512, nullable=False),
        ColumnDefinition("namespace", "string", length=128, nullable=False, default="default"),
        ColumnDefinition("pod_name", "string", length=128, nullable=False, default="agentserver"),
        ColumnDefinition("container_name", "string", length=128, nullable=False, default="agent"),
        ColumnDefinition("container_port", "integer", nullable=False, default=8080),
        ColumnDefinition("port_name", "string", length=64, nullable=False, default="http"),
        ColumnDefinition("sse_port", "integer", nullable=False, default=8080),
        ColumnDefinition("sse_path", "string", length=128, nullable=False, default="/sse"),
        ColumnDefinition("image_pull_policy", "string", length=64, nullable=False,
                         default="IfNotPresent"),
        ColumnDefinition("kubeconfig", "string", length=512, nullable=True),
        ColumnDefinition("readiness_initial_delay", "integer", nullable=False, default=5),
        ColumnDefinition("readiness_period", "integer", nullable=False, default=5),
        ColumnDefinition("ready_timeout", "integer", nullable=False, default=300),
        ColumnDefinition("ready_poll_interval", "integer", nullable=False, default=2),
        ColumnDefinition("nfs_server", "string", length=256, nullable=True),
        ColumnDefinition("nfs_path", "string", length=256, nullable=True),
        ColumnDefinition("nfs_mount_path", "string", length=256, nullable=True),
        ColumnDefinition("agent_cpu_request", "string", length=32, nullable=True),
        ColumnDefinition("agent_memory_request", "string", length=32, nullable=True),
        ColumnDefinition("agent_cpu_limit", "string", length=32, nullable=True),
        ColumnDefinition("agent_memory_limit", "string", length=32, nullable=True),
        ColumnDefinition("min_idle_services", "integer", nullable=False, default=0),
        ColumnDefinition("service_concurrency", "integer", nullable=False, default=2),
        ColumnDefinition("service_ttl", "integer", nullable=False, default=300),
        ColumnDefinition("session_concurrency", "integer", nullable=False, default=3),
        ColumnDefinition("session_ttl", "integer", nullable=False, default=60),
        ColumnDefinition("message_timeout", "integer", nullable=False, default=600),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

ROUTING_RULE_TABLE_DEF = TableDefinition(
    table_name=ROUTING_RULE_TABLE,
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False, default=""),
        ColumnDefinition("rule_id", "string", length=100, nullable=False, unique=True),
        ColumnDefinition("group_id", "string", length=128, nullable=False),
        ColumnDefinition("bot_id", "string", length=128, nullable=False),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

# Template 字段 ↔ DB 列名（HLD 名 → EE 兼容列名）
_COLUMN_OF: dict[str, str] = {
    "template_id": "template_id",
    "template_name": "template_name",
    "description": "description",
    "agent_image": "agent_image",
    "namespace": "namespace",
    "pod_name": "pod_name",
    "container_name": "container_name",
    "container_port": "container_port",
    "sse_port": "sse_port",
    "sse_path": "sse_path",
    "image_pull_policy": "image_pull_policy",
    "kubeconfig": "kubeconfig",
    "readiness_initial_delay": "readiness_initial_delay",
    "readiness_period": "readiness_period",
    "ready_timeout": "ready_timeout",
    "ready_poll_interval": "ready_poll_interval",
    "nfs_server": "nfs_server",
    "nfs_path": "nfs_path",
    "nfs_mount_path": "nfs_mount_path",
    "agent_cpu_request": "agent_cpu_request",
    "agent_memory_request": "agent_memory_request",
    "agent_cpu_limit": "agent_cpu_limit",
    "agent_memory_limit": "agent_memory_limit",
    "min_idle_pods": "min_idle_services",
    "pod_concurrency": "service_concurrency",
    "pod_ttl": "service_ttl",
    "scope_concurrency": "session_concurrency",
    "session_ttl": "session_ttl",
    "message_timeout": "message_timeout",
    "enabled": "enabled",
    "data": "data",
}

_INT_FIELDS = frozenset({
    "container_port", "sse_port", "readiness_initial_delay", "readiness_period",
    "ready_timeout", "ready_poll_interval", "min_idle_pods", "pod_concurrency",
    "pod_ttl", "scope_concurrency", "session_ttl", "message_timeout",
})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def template_from_row(row: Any) -> Template:
    """DB 行 → Template 业务对象（未命中 enabled=False 的模板仍返回，调用方判定）。"""
    kwargs: dict[str, Any] = {}
    for field_name, column in _COLUMN_OF.items():
        value = getattr(row, column, None)
        if field_name in _INT_FIELDS and value is not None:
            value = int(value)
        kwargs[field_name] = value
    return Template(**kwargs)


def row_from_template(t: Template) -> dict[str, Any]:
    now = _utcnow()
    row = {column: getattr(t, field) for field, column in _COLUMN_OF.items()}
    row["jiuwenclaw_id"] = TENANT_ID
    row["created_at"] = now
    row["updated_at"] = now
    return row


def row_from_template_for_update(t: Template) -> dict[str, Any]:
    """update 用行：不带 created_at / id（保留原创建时间与自增主键）。"""
    row = row_from_template(t)
    row.pop("created_at", None)
    row.pop("id", None)
    return row


def template_from_payload(template_id: str, payload: dict[str, Any]) -> Template:
    """config_sync 下发的 template dict → Template（未给字段用默认值）。"""
    defaults = Template(template_id=template_id)
    kwargs: dict[str, Any] = {"template_id": template_id}
    for field in _COLUMN_OF:
        if field == "template_id":
            continue
        if field in payload and payload[field] is not None:
            value = payload[field]
            kwargs[field] = int(value) if field in _INT_FIELDS else value
        else:
            kwargs[field] = getattr(defaults, field)
    return Template(**kwargs)


# 池参数推送回调：config_sync → rm_facade.update_pool_config(scope_id, pool, pod_spec?)
PoolConfigPush = Callable[[str, dict[str, Any], dict[str, Any] | None], Awaitable[None]]

CONFIG_SYNC_LOCK_TTL = 60  # 串行化锁 TTL（处理超时上限）


class ConfigStore:
    """template / routing_rule 的 DB 存取 + resolve 缓存 + config_sync 编排。"""

    def __init__(
        self,
        db: Any,
        sm_state: SessionState,
        push_pool_config: PoolConfigPush | None = None,
    ) -> None:
        self._db = db
        self.state = sm_state
        self._push = push_pool_config

    # -------------------------------------------------------------- 读路径

    async def get_template(self, template_id: str) -> Template | None:
        row = await self._db.get(TEMPLATE_TABLE, {"template_id": template_id})
        return template_from_row(row) if row is not None else None

    async def list_rules(self) -> list[dict[str, str]]:
        rows = await self._db.list_records(ROUTING_RULE_TABLE, limit=10_000)
        return [
            {
                "rule_id": s(getattr(r, "rule_id")),
                "group_id": s(getattr(r, "group_id")),
                "bot_id": s(getattr(r, "bot_id")),
                "template_id": s(getattr(r, "template_id")),
            }
            for r in rows
        ]

    async def resolve(self, scope_id: str, group_id: str, bot_id: str) -> Template:
        """(group_id, bot_id) → template：缓存命中直接返回；miss 读 DB 并回写缓存。

        无匹配（或模板禁用/不存在）→ ConfigNotFound(503)。缓存由 config_sync 主动失效。
        """
        cached = await self._load_cache(scope_id)
        if cached is not None:
            return self._template_from_cache(cached)

        template = await self._resolve_from_db(group_id, bot_id)
        await self._write_cache(scope_id, template)
        return template

    async def _resolve_from_db(self, group_id: str, bot_id: str) -> Template:
        rules = await self.list_rules()
        # 优先级：精确 > (group, *) > (*, bot) > (*, *)
        for want_group, want_bot in (
            (group_id, bot_id), (group_id, "*"), ("*", bot_id), ("*", "*")
        ):
            for rule in rules:
                if rule["group_id"] == want_group and rule["bot_id"] == want_bot:
                    template = await self.get_template(rule["template_id"])
                    if template is not None and template.enabled:
                        return template
        raise ConfigNotFound(
            f"no routing rule/template matches (group_id={group_id!r}, bot_id={bot_id!r})"
        )

    # -------------------------------------------------------------- 缓存

    async def _load_cache(self, scope_id: str) -> dict[str, str] | None:
        raw = await self.state.redis.hgetall(self.state.k.scope_config(scope_id))
        if not raw:
            return None
        h = {s(k): s(v) for k, v in raw.items()}
        template_id = h.get("template_id")
        if not template_id:
            return None
        # 缓存行携带完整 template JSON（deploy 子集在 need_acquire 时要用）
        return h

    async def _write_cache(self, scope_id: str, template: Template) -> None:
        """缓存 = ScopeConfig 字段 + template JSON（一次 HSET，读路径零 DB）。"""
        from .models import ScopeConfig  # 局部 import 避免环

        cfg = ScopeConfig(
            scope_id=scope_id,
            template_id=template.template_id,
            scope_concurrency=template.scope_concurrency,
            pod_concurrency=template.pod_concurrency,
            session_ttl=template.session_ttl,
            pod_ttl=template.pod_ttl,
            min_idle_pods=template.min_idle_pods,
            max_pods=template.max_pods,
            deploy_ver=template.deploy_ver(),
            ver=str(now_ts()),
        )
        mapping = cfg.to_hash()
        mapping["template_json"] = json.dumps(template.deploy_subset())
        await self.state.redis.hset(
            self.state.k.scope_config(scope_id),
            mapping={k: v for k, v in mapping.items()},
        )

    def _template_from_cache(self, h: dict[str, str]) -> Template:
        """缓存 → Template 视图（策略字段 + deploy 子集即可，元信息不缓存）。"""
        payload = json.loads(h.get("template_json") or "{}")
        payload.update({
            "template_id": h.get("template_id", ""),
            "scope_concurrency": int(h.get("scope_concurrency", 0)),
            "pod_concurrency": int(h.get("pod_concurrency", 1)),
            "session_ttl": int(h.get("session_ttl", 60)),
            "pod_ttl": int(h.get("pod_ttl", 300)),
            "min_idle_pods": int(h.get("min_idle_pods", 0)),
        })
        return template_from_payload(h.get("template_id", ""), payload)

    async def invalidate_cache(self, scope_id: str) -> None:
        """B 类失效：DEL 缓存，下次 route 重新 resolve。"""
        await self.state.redis.delete(self.state.k.scope_config(scope_id))

    # -------------------------------------------------------------- config_sync

    async def config_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        """处理一次配置下发（场景 M）。返回 {ok, synced?, deleted?}。"""
        kind = str(payload.get("kind") or "")
        op = str(payload.get("op") or "")
        if kind not in ("template", "routing_rule"):
            raise InvalidParams(f"unknown kind={kind!r}")
        if op not in ("create", "update", "delete", "sync"):
            raise InvalidParams(f"unknown op={op!r}")

        token = f"cfgsync-{now_ts()}"
        if not await self.state.try_lock(
            self.state.k.lock_config_sync(), CONFIG_SYNC_LOCK_TTL, token
        ):
            raise ConfigSyncBusy("a previous config_sync is still in progress")
        try:
            return await self._config_sync_locked(kind, op, payload)
        finally:
            await self.state.unlock(self.state.k.lock_config_sync(), token)

    async def _config_sync_locked(
        self, kind: str, op: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if kind == "template":
            return await self._sync_template(op, payload)
        return await self._sync_routing_rule(op, payload)

    # ---- template

    async def _sync_template(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        if op == "sync":
            templates = payload.get("templates") or []
            if not isinstance(templates, list):
                raise InvalidParams("templates must be a list")
            old_by_id = {t.template_id: t for t in await self._all_templates()}
            # 全量同步：以数组为准（增删改）
            incoming: dict[str, Template] = {}
            for item in templates:
                tid = str(item.get("template_id") or "")
                if not tid:
                    raise InvalidParams("template item missing template_id")
                incoming[tid] = template_from_payload(tid, item.get("template") or item)
            synced = deleted = 0
            for tid, template in incoming.items():
                await self._upsert_template(template)
                synced += 1
            for tid in set(old_by_id) - set(incoming):
                await self._db.delete(TEMPLATE_TABLE, {"template_id": tid})
                deleted += 1
            affected = await self._propagate_template_change(
                incoming, old_by_id
            )
            return {"ok": True, "synced": synced, "deleted": deleted, "affected_scopes": affected}

        template_id = str(payload.get("template_id") or "")
        if not template_id:
            raise InvalidParams("template_id is required")

        if op == "create":
            template = template_from_payload(
                template_id, payload.get("template") or payload
            )
            await self._db.create(TEMPLATE_TABLE, row_from_template(template))
            return {"ok": True, "synced": 1, "deleted": 0, "affected_scopes": []}

        if op == "delete":
            old = await self.get_template(template_id)
            ok = await self._db.delete(TEMPLATE_TABLE, {"template_id": template_id})
            if ok and old is not None:
                # 模板删除：引用它的 scope 缓存失效（下次 resolve → CONFIG_NOT_FOUND）
                affected = await self._cached_scopes_of_templates({template_id})
                for scope_id in affected:
                    await self.invalidate_cache(scope_id)
                return {"ok": True, "synced": 0, "deleted": 1, "affected_scopes": affected}
            return {"ok": True, "synced": 0, "deleted": 0, "affected_scopes": []}

        # op == update：老值（DB 现行行）与新值（payload）进程内逐字段 diff
        old = await self.get_template(template_id)
        if old is None:
            raise InvalidParams(f"template {template_id!r} not found")
        updates = payload.get("updates") or {}
        if not isinstance(updates, dict):
            raise InvalidParams("updates must be an object")
        new = template_from_payload(
            template_id, {**{f: getattr(old, f) for f in _COLUMN_OF}, **updates}
        )
        await self._db.update(
            TEMPLATE_TABLE, {"template_id": template_id}, row_from_template_for_update(new)
        )
        affected = await self._propagate_template_change({template_id: new}, {template_id: old})
        return {"ok": True, "synced": 1, "deleted": 0, "affected_scopes": affected}

    async def _upsert_template(self, template: Template) -> None:
        if await self.get_template(template.template_id) is not None:
            await self._db.update(
                TEMPLATE_TABLE,
                {"template_id": template.template_id},
                row_from_template_for_update(template),
            )
        else:
            await self._db.create(TEMPLATE_TABLE, row_from_template(template))

    async def _all_templates(self) -> list[Template]:
        rows = await self._db.list_records(TEMPLATE_TABLE, limit=10_000)
        return [template_from_row(r) for r in rows]

    # ---- 变更扩散（diff / A-B 类 / 推送；仅 update/sync 路径调用）

    async def _propagate_template_change(
        self,
        new_by_id: dict[str, Template],
        old_by_id: dict[str, Template],
    ) -> list[str]:
        """对每个变更 template：找引用它的 scope，按 A/B 类扩散（场景 M）。

        返回受影响 scope 列表。写 DB 已完成（调用方保证），此处只做缓存/软摘除/推送。
        """
        changed_ids = {
            tid
            for tid, new in new_by_id.items()
            if tid not in old_by_id or self._diff_class(new, old_by_id[tid]) != "none"
        }
        if not changed_ids:
            return []

        # 完成判定：受影响 scope 若仍有「已日落待回收」的中间态 Pod → 拒绝本次下发
        affected = await self._cached_scopes_of_templates(changed_ids)
        for scope_id in affected:
            pending = await self._sunset_pending_pods(scope_id)
            if pending:
                raise ConfigSyncBusy(
                    f"scope {scope_id} still has sunset pods pending reclaim: {pending}"
                )

        for scope_id in affected:
            # 该 scope 当前生效的新模板（按缓存 template_id → DB 新行；缓存可能已失效）
            h = {s(k): s(v) for k, v in
                 (await self.state.redis.hgetall(self.state.k.scope_config(scope_id))).items()}
            template = new_by_id.get(h.get("template_id", ""))
            if template is None:
                # 模板被删除 / 不在本次变更集：直接失效缓存
                await self.invalidate_cache(scope_id)
                continue

            old = old_by_id.get(template.template_id)
            diff_class = self._diff_class(template, old) if old else "none"
            if diff_class == "A":
                # A 类：软摘除老版本 Pod（ZREM 出候选集，不接新流量；存量会话不受影响）
                removed = await self._soft_remove_stale_pods(scope_id, template.deploy_ver())
                logger.info(
                    "config_sync A-class sunset: scope=%s removed_pods=%s new_ver=%s",
                    scope_id, removed, template.deploy_ver(),
                )
                await self._write_cache(scope_id, template)
                if self._push:
                    await self._push(scope_id, template.pool_config(), template.deploy_subset())
            else:
                # B 类（或新增引用）：DEL 缓存 + 推池参数，立即生效
                await self.invalidate_cache(scope_id)
                if self._push:
                    await self._push(scope_id, template.pool_config(), None)
        return affected

    @staticmethod
    def _diff_class(new: Template, old: Template | None) -> str:
        """A/B 类判定：deploy_ver 不等 → 'A'；仅策略字段变 → 'B'；无变化 → 'none'。"""
        if old is None:
            return "B"
        if new.deploy_ver() != old.deploy_ver():
            return "A"
        return "B" if any(
            getattr(new, f) != getattr(old, f) for f in POLICY_FIELDS
        ) else "none"

    async def _cached_scopes_of_templates(self, template_ids: set[str]) -> list[str]:
        """扫 scope:config 缓存，返回 template_id 命中变更集的 scope 列表。"""
        if not template_ids:
            return []
        pattern = f"{self.state.prefix}scope:*:config"
        cursor = 0
        scopes: list[str] = []
        while True:
            cursor, keys = await self.state.redis.scan(cursor, match=pattern, count=200)
            for key in keys:
                parts = s(key).split(":")
                scope_id = parts[-2]
                tid = s(await self.state.redis.hget(s(key), "template_id"))
                if tid in template_ids:
                    scopes.append(scope_id)
            if int(cursor or 0) == 0:
                break
        return sorted(set(scopes))

    async def _soft_remove_stale_pods(self, scope_id: str, new_deploy_ver: str) -> list[str]:
        """把 deploy_ver 不匹配的 Pod ZREM 出 scope:pods 候选集（与 idle_consider 同款机制）。"""
        removed: list[str] = []
        for pod_id in await self.state.scope_pod_ids(scope_id):
            if await self.state.pod_deploy_ver(scope_id, pod_id) != new_deploy_ver:
                await self.state.redis.zrem(self.state.k.scope_pods(scope_id), pod_id)
                removed.append(pod_id)
        return removed

    async def _sunset_pending_pods(self, scope_id: str) -> list[str]:
        """中间态判定：在 pods:registered 属于该 scope、但已不在 scope:pods 候选集的 Pod。"""
        registered = await self.state.registered_pods()
        prefix = f"{scope_id}:"
        in_candidates = set(await self.state.scope_pod_ids(scope_id))
        return [
            entry[len(prefix):]
            for entry in registered
            if entry.startswith(prefix) and entry[len(prefix):] not in in_candidates
        ]

    # ---- routing_rule

    async def _sync_routing_rule(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        if op == "sync":
            rules = payload.get("rules") or []
            if not isinstance(rules, list):
                raise InvalidParams("rules must be a list")
            existing = {r["rule_id"]: r for r in await self.list_rules()}
            incoming: dict[str, dict[str, str]] = {}
            for item in rules:
                rule = _rule_from_payload(item)
                incoming[rule["rule_id"]] = rule
            now = _utcnow()
            for rule in incoming.values():
                row = {**rule, "jiuwenclaw_id": TENANT_ID,
                       "created_at": now, "updated_at": now}
                if rule["rule_id"] in existing:
                    await self._db.update(
                        ROUTING_RULE_TABLE, {"rule_id": rule["rule_id"]}, row
                    )
                else:
                    await self._db.create(ROUTING_RULE_TABLE, row)
            for rule_id in set(existing) - set(incoming):
                await self._db.delete(ROUTING_RULE_TABLE, {"rule_id": rule_id})
            await self._invalidate_all_scope_caches()
            return {"ok": True, "synced": len(incoming),
                    "deleted": len(set(existing) - set(incoming)), "affected_scopes": []}

        rule_id = str(payload.get("rule_id") or "")
        if not rule_id:
            raise InvalidParams("rule_id is required")

        if op == "create":
            rule = _rule_from_payload(payload)
            now = _utcnow()
            await self._db.create(
                ROUTING_RULE_TABLE,
                {**rule, "jiuwenclaw_id": TENANT_ID,
                 "created_at": now, "updated_at": now},
            )
            await self._invalidate_all_scope_caches()
            return {"ok": True, "synced": 1, "deleted": 0, "affected_scopes": []}

        if op == "delete":
            await self._db.delete(ROUTING_RULE_TABLE, {"rule_id": rule_id})
            await self._invalidate_all_scope_caches()
            return {"ok": True, "synced": 0, "deleted": 1, "affected_scopes": []}

        # op == update
        updates = payload.get("updates") or {}
        row = await self._db.get(ROUTING_RULE_TABLE, {"rule_id": rule_id})
        if row is None:
            raise InvalidParams(f"routing rule {rule_id!r} not found")
        merged = {
            "rule_id": rule_id,
            "group_id": updates.get("group_id", s(getattr(row, "group_id"))),
            "bot_id": updates.get("bot_id", s(getattr(row, "bot_id"))),
            "template_id": updates.get("template_id", s(getattr(row, "template_id"))),
        }
        await self._db.update(
            ROUTING_RULE_TABLE, {"rule_id": rule_id},
            {**merged, "updated_at": _utcnow()},
        )
        await self._invalidate_all_scope_caches()
        return {"ok": True, "synced": 1, "deleted": 0, "affected_scopes": []}

    async def _invalidate_all_scope_caches(self) -> None:
        """路由规则变更无法定位受影响 scope（缓存无 group/bot）→ 全量失效（resolve 便宜）。"""
        pattern = f"{self.state.prefix}scope:*:config"
        cursor = 0
        while True:
            cursor, keys = await self.state.redis.scan(cursor, match=pattern, count=200)
            for key in keys:
                await self.state.redis.delete(s(key))
            if int(cursor or 0) == 0:
                break


def _rule_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    rule_id = str(payload.get("rule_id") or "")
    group_id = str(payload.get("group_id") or "")
    bot_id = str(payload.get("bot_id") or "")
    template_id = str(payload.get("template_id") or "")
    if not (rule_id and group_id and bot_id and template_id):
        raise InvalidParams(
            f"routing rule requires rule_id/group_id/bot_id/template_id, got {payload!r}"
        )
    return {"rule_id": rule_id, "group_id": group_id,
            "bot_id": bot_id, "template_id": template_id}
