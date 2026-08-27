# coding: utf-8
"""Config 层（scope 重构版）：template / routing_scope 持久化 + 快照 + config_sync。

- 存储在共享 DB（config system-of-record），表 ``service_config_template`` /
  ``routing_scope``（scope_id / match_index / template_id / routing_rules JSON）；
- ``resolve(user_id, group_id, bot_id)``：route 热路径的路由解析——读 Redis
  单键快照 ``routing:snapshot``（scopes+templates 全量 JSON），进程内按
  (index ASC, scope_id ASC) first-fit 求值；快照缺失/损坏 → 从 DB 重建；
- ``config_sync``：Claw Manager 全量下发入口（场景 M）——``{templates, scopes}``
  快照式替换（旧 kind/op 增量协议已废弃）。锁内编排：校验 → 日落中间态检查
  （先于写库,拒绝时零副作用）→ 写 DB → 重建快照 → 逐 scope 推 RM 池参数
  （**始终带 pod_spec**——无请求 scope 的 min_idle 预热依赖它）→ A 类软摘除
  老版本 Pod → 被删 scope 推 min_idle=0 自然排空。
  全程持 ``lock:config_sync`` 串行化（忙 → 409 CONFIG_SYNC_BUSY）。

红线：写 DB 失败立即中止，不得 SET 快照、不得推送（防 last-known-good 被污染）。
"""

from __future__ import annotations

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
from .routing import (
    RoutingScopeDef,
    RoutingSnapshot,
    build_snapshot,
    has_wildcard_scope,
    match_scope,
    parse_routing_expr,
    parse_scope,
    snapshot_from_json,
    snapshot_to_json,
)
from .state import SessionState

logger = logging.getLogger("agent_runtime.session_manager")

TENANT_ID = ""  # v1 单租户；列保留为 EE 兼容

TEMPLATE_TABLE = "service_config_template"
ROUTING_SCOPE_TABLE = "routing_scope"

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
        ColumnDefinition("health_path", "string", length=128, nullable=False,
                         default="/health"),
        ColumnDefinition("agent_env", "json", nullable=True),
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

ROUTING_SCOPE_TABLE_DEF = TableDefinition(
    table_name=ROUTING_SCOPE_TABLE,
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False, default=""),
        ColumnDefinition("scope_id", "string", length=128, nullable=False, unique=True),
        # 列名不用 index（SQL 保留字）
        ColumnDefinition("match_index", "integer", nullable=False, default=0),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        # 结构化规则原样落库（wire 格式；空列表/NULL = 通配 scope）
        ColumnDefinition("routing_rules", "json", nullable=True),
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
    "health_path": "health_path",
    "agent_env": "agent_env",
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
    # 老行/NULL 防御:agent_env 非 dict → 空表;health_path 空 → 默认
    if not isinstance(kwargs.get("agent_env"), dict):
        kwargs["agent_env"] = {}
    if not kwargs.get("health_path"):
        kwargs["health_path"] = "/health"
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
            if field == "agent_env":
                if not isinstance(value, dict) or any(
                        not isinstance(k, str) or not isinstance(v, (str, int, float, bool))
                        for k, v in value.items()):
                    raise InvalidParams(
                        "agent_env must be an object mapping string keys to "
                        f"scalar values, got {value!r}"
                    )
                value = {k: str(v) for k, v in value.items()}
            kwargs[field] = int(value) if field in _INT_FIELDS else value
        else:
            kwargs[field] = getattr(defaults, field)
    return Template(**kwargs)


def _scope_from_row(row: Any) -> RoutingScopeDef | None:
    """routing_scope 行 → RoutingScopeDef；行损坏（手改 DB 等）跳过并告警。"""
    try:
        expr_raw = getattr(row, "routing_rules", None)
        if expr_raw is None:
            expr_raw = ""
        if not isinstance(expr_raw, str):
            raise ValueError(
                f"routing_rules must be a string expression, got {type(expr_raw).__name__}"
            )
        return RoutingScopeDef(
            scope_id=s(getattr(row, "scope_id")),
            index=int(getattr(row, "match_index") or 0),
            template_id=s(getattr(row, "template_id")),
            expr=expr_raw,
            rule=parse_routing_expr(expr_raw) if expr_raw.strip() else None,
        )
    except Exception:  # noqa: BLE001 - 读路径对坏行容错（写路径已强校验）
        logger.warning(
            "routing_scope row corrupt, skipped: scope_id=%r",
            getattr(row, "scope_id", "?"), exc_info=True,
        )
        return None


# 池参数推送回调：config_sync → rm_facade.update_pool_config(scope_id, pool, pod_spec?)
PoolConfigPush = Callable[[str, dict[str, Any], dict[str, Any] | None], Awaitable[None]]

CONFIG_SYNC_LOCK_TTL = 60  # 串行化锁 TTL（处理超时上限）


class ConfigStore:
    """template / routing_scope 的 DB 存取 + 路由快照 + config_sync 编排。"""

    def __init__(
        self,
        db: Any,
        sm_state: SessionState,
        push_pool_config: PoolConfigPush | None = None,
    ) -> None:
        self._db = db
        self.state = sm_state
        self._push = push_pool_config
        # 进程内快照 memo：原始串相等则复用已解析对象（route 热路径零 json.loads）
        self._snapshot_raw: str | None = None
        self._snapshot: RoutingSnapshot | None = None

    # -------------------------------------------------------------- 读路径

    async def get_template(self, template_id: str) -> Template | None:
        row = await self._db.get(TEMPLATE_TABLE, {"template_id": template_id})
        return template_from_row(row) if row is not None else None

    async def list_templates(self, limit: int = 200) -> list[dict[str, Any]]:
        """诊断只读：模板摘要（HLD 字段名；kubeconfig 等敏感列由 /debug 层脱敏）。"""
        rows = await self._db.list_records(TEMPLATE_TABLE, limit=limit)
        out: list[dict[str, Any]] = []
        for r in rows:
            t = template_from_row(r)
            if t is None:
                continue
            out.append({
                "template_id": t.template_id,
                "enabled": bool(t.enabled),
                "agent_image": t.agent_image,
                "namespace": t.namespace,
                "scope_concurrency": t.scope_concurrency,
                "session_ttl": t.session_ttl,
                "pod_concurrency": t.pod_concurrency,
                "max_pods": t.max_pods,
                "min_idle_pods": t.min_idle_pods,
                "pod_ttl": t.pod_ttl,
                "kubeconfig": t.kubeconfig,
            })
        return out

    async def list_scopes(self, limit: int = 100_000) -> list[RoutingScopeDef]:
        """全部 scope 定义（快照重建 / /debug 用；坏行跳过）。"""
        rows = await self._db.list_records(ROUTING_SCOPE_TABLE, limit=limit)
        out: list[RoutingScopeDef] = []
        for r in rows:
            scope = _scope_from_row(r)
            if scope is not None:
                out.append(scope)
        return out

    async def resolve(
        self, user_id: str | None, group_id: str, bot_id: str
    ) -> tuple[str, Template]:
        """(user_id, group_id, bot_id) → (scope_id, Template)。

        读单键快照求值（index 升序 first-fit）；无匹配 → ConfigNotFound(503)。
        """
        snapshot = await self._load_snapshot()
        scope = match_scope(snapshot, user_id, group_id, bot_id)
        if scope is None:
            logger.warning(
                "resolve no matching scope: user_id=%r group_id=%r bot_id=%r scopes=%d",
                user_id, group_id, bot_id, len(snapshot.scopes),
            )
            raise ConfigNotFound(
                f"no routing scope matches (user_id={user_id!r}, "
                f"group_id={group_id!r}, bot_id={bot_id!r})"
            )
        logger.debug(
            "resolve matched: scope=%s template=%s index=%d",
            scope.scope_id, scope.template_id, scope.index,
        )
        return scope.scope_id, snapshot.templates[scope.template_id]

    # -------------------------------------------------------------- 路由快照

    async def _load_snapshot(self) -> RoutingSnapshot:
        """读快照：memo 命中直接回；缺失/损坏 → DB 重建（冷启动自愈）。"""
        raw = await self.state.routing_snapshot_raw()
        if raw:
            if self._snapshot is not None and raw == self._snapshot_raw:
                return self._snapshot
            try:
                snapshot = snapshot_from_json(raw)
            except ValueError:
                logger.warning("routing snapshot corrupt, rebuilding from db")
            else:
                self._snapshot_raw, self._snapshot = raw, snapshot
                return snapshot
        return await self.rebuild_snapshot()

    async def rebuild_snapshot(self) -> RoutingSnapshot:
        """DB → 快照 → 原子 SET（config_sync / 冷启动重建的唯一写点）。"""
        templates = await self._all_templates()
        scopes = await self.list_scopes()
        snapshot = build_snapshot(scopes, templates, ver=now_ts())
        text = snapshot_to_json(snapshot)
        await self.state.write_routing_snapshot(text)
        self._snapshot_raw, self._snapshot = text, snapshot
        logger.info(
            "routing snapshot rebuilt: templates=%d scopes=%d ver=%s",
            len(snapshot.templates), len(snapshot.scopes), snapshot.ver,
        )
        return snapshot

    async def ensure_snapshot(self) -> RoutingSnapshot:
        """启动期无条件重建（消除 lifespan 后首次 route 的冷启动窗口）。"""
        return await self.rebuild_snapshot()

    async def routing_snapshot_view(self) -> RoutingSnapshot:
        """诊断只读（/debug 用）：当前生效快照（缺失会触发重建）。"""
        return await self._load_snapshot()

    # -------------------------------------------------------------- config_sync

    async def config_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        """处理一次全量配置下发（场景 M）。返回统计 + affected_scopes。"""
        templates_in, scopes_in, wildcard = self._parse_payload(payload)

        token = f"cfgsync-{now_ts()}"
        if not await self.state.try_lock(
            self.state.k.lock_config_sync(), CONFIG_SYNC_LOCK_TTL, token
        ):
            raise ConfigSyncBusy("a previous config_sync is still in progress")
        try:
            return await self._config_sync_locked(templates_in, scopes_in, wildcard)
        finally:
            await self.state.unlock(self.state.k.lock_config_sync(), token)

    @staticmethod
    def _parse_payload(
        payload: dict[str, Any],
    ) -> tuple[dict[str, Template], dict[str, RoutingScopeDef], bool]:
        """锁外校验（纯 CPU，确定性 400）。返回 (templates_in, scopes_in, 通配存在)。"""
        if not isinstance(payload, dict):
            raise InvalidParams("config_sync payload must be an object")
        if "kind" in payload or "op" in payload:
            raise InvalidParams(
                "legacy kind/op payload no longer supported; "
                "send {templates: [...], scopes: [...]} full snapshot"
            )
        templates_raw = payload.get("templates")
        scopes_raw = payload.get("scopes")
        if not isinstance(templates_raw, list) or not isinstance(scopes_raw, list):
            raise InvalidParams("templates and scopes must both be lists")

        templates_in: dict[str, Template] = {}
        for item in templates_raw:
            if not isinstance(item, dict):
                raise InvalidParams("template item must be an object")
            tid = str(item.get("template_id") or "")
            if not tid:
                raise InvalidParams("template item missing template_id")
            if tid in templates_in:
                raise InvalidParams(f"duplicate template_id {tid!r}")
            templates_in[tid] = template_from_payload(tid, item)

        scopes_in: dict[str, RoutingScopeDef] = {}
        for item in scopes_raw:
            scope = parse_scope(item, set(templates_in))
            if scope.scope_id in scopes_in:
                raise InvalidParams(f"duplicate scope_id {scope.scope_id!r}")
            scopes_in[scope.scope_id] = scope

        wildcard = has_wildcard_scope(scopes_in.values())
        if not wildcard:
            logger.warning(
                "config_sync payload has NO wildcard scope (empty routing_rules); "
                "unmatched requests will get CONFIG_NOT_FOUND"
            )
        return templates_in, scopes_in, wildcard

    async def _config_sync_locked(
        self,
        templates_in: dict[str, Template],
        scopes_in: dict[str, RoutingScopeDef],
        wildcard: bool,
    ) -> dict[str, Any]:
        # ---- 旧态（DB system-of-record）
        old_templates = {t.template_id: t for t in await self._all_templates()}
        old_scopes = {scope.scope_id: scope for scope in await self.list_scopes()}

        # ---- diff：模板变更集 / 引用切换 / 需日落的 scope
        changed_ids = {
            tid for tid, new in templates_in.items()
            if tid not in old_templates
            or self._diff_class(new, old_templates[tid]) != "none"
        }
        ref_switched = {
            sid for sid, scope in scopes_in.items()
            if sid in old_scopes and old_scopes[sid].template_id != scope.template_id
        }
        # 日落判定 = 该 scope 的「有效模板」deploy_ver 前后不同（引用切换或模板 A 类变更）
        sunset_scopes: list[str] = []
        for sid, scope in scopes_in.items():
            old_scope = old_scopes.get(sid)
            if old_scope is None:
                continue  # 新 scope，无存量 Pod
            new_tpl = templates_in[scope.template_id]
            old_ref = old_templates.get(old_scope.template_id)
            if old_ref is None:
                sunset_scopes.append(sid)  # 旧引用模板行缺失（防御）：按 A 类处理
            elif old_ref.template_id == new_tpl.template_id:
                if self._diff_class(new_tpl, old_ref) == "A":
                    sunset_scopes.append(sid)
            elif new_tpl.deploy_ver() != old_ref.deploy_ver():
                sunset_scopes.append(sid)  # 引用切换且 deploy_ver 变
        affected = sorted(
            {sid for sid, scope in scopes_in.items() if scope.template_id in changed_ids}
            | ref_switched
        )

        # ---- 日落中间态检查（★先于写库：拒绝时 DB/Redis 均未动；沿用 M 期语义，
        #      对全部受影响 scope 生效——pending 意味着上一轮日落尚未回收完毕）
        for sid in affected:
            pending = await self._sunset_pending_pods(sid)
            if pending:
                raise ConfigSyncBusy(
                    f"scope {sid} still has sunset pods pending reclaim: {pending}"
                )

        # ---- 写 DB（快照式替换；红线：任一失败立即上抛，不动快照、不推送）
        for template in templates_in.values():
            await self._upsert_template(template)
        for tid in set(old_templates) - set(templates_in):
            await self._db.delete(TEMPLATE_TABLE, {"template_id": tid})
        for scope in scopes_in.values():
            await self._upsert_scope(scope)
        for sid in set(old_scopes) - set(scopes_in):
            await self._db.delete(ROUTING_SCOPE_TABLE, {"scope_id": sid})

        # ---- 重建快照（DB 读回最终态 → 原子 SET；B 类立即生效由此完成）
        await self.rebuild_snapshot()

        # ---- 扩散①：eager 预热——每个存活 scope 推池参数 + pod_spec（必须带 spec，
        #      RM 才会落 pod_spec_json/deploy_ver，autoscale 才能无请求预热 min_idle）
        for sid, scope in scopes_in.items():
            template = templates_in[scope.template_id]
            await self._push_or_warn(sid, template.pool_config(), template.deploy_subset())

        # ---- 扩散②：A 类日落——软摘除老版本 Pod（ZREM 出候选集，不接新流量；
        #      存量会话亲和不受影响，空闲老 Pod 由 reclaim 按 pod_ttl 回收）
        for sid in sunset_scopes:
            scope = scopes_in[sid]
            new_ver = templates_in[scope.template_id].deploy_ver()
            removed = await self._soft_remove_stale_pods(sid, new_ver)
            logger.info(
                "config_sync A-class sunset: scope=%s removed_pods=%s new_ver=%s",
                sid, removed, new_ver,
            )

        # ---- 扩散③：被删 scope → 推 min_idle=0（停预热自然排空；存量会话到期止）
        for sid in sorted(set(old_scopes) - set(scopes_in)):
            old_tpl = old_templates.get(old_scopes[sid].template_id)
            pool = (
                {**old_tpl.pool_config(), "min_idle_pods": 0}
                if old_tpl is not None
                else {"min_idle_pods": 0, "max_pods": 1, "pod_ttl": 300}
            )
            await self._push_or_warn(sid, pool, None)

        return {
            "ok": True,
            "templates_synced": len(templates_in),
            "templates_deleted": len(set(old_templates) - set(templates_in)),
            "scopes_synced": len(scopes_in),
            "scopes_deleted": len(set(old_scopes) - set(scopes_in)),
            "affected_scopes": affected,
            "wildcard_present": wildcard,
        }

    async def _push_or_warn(
        self,
        scope_id: str,
        pool: dict[str, Any],
        pod_spec: dict[str, Any] | None,
    ) -> None:
        """推 RM 池参数；失败仅告警不中止（DB/快照已一致，下次下发或首见 acquire 收敛）。"""
        if self._push is None:
            return
        try:
            await self._push(scope_id, pool, pod_spec)
        except Exception:  # noqa: BLE001
            logger.exception(
                "push pool config failed (scope=%s fields=%s) -- "
                "warm-up deferred to next config_sync/first acquire",
                scope_id, sorted(pool),
            )

    # ---- template DB 存取

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

    # ---- scope DB 存取

    async def _upsert_scope(self, scope: RoutingScopeDef) -> None:
        row = {
            "jiuwenclaw_id": TENANT_ID,
            "scope_id": scope.scope_id,
            "match_index": scope.index,
            "template_id": scope.template_id,
            # 原始表达式串(空 = 通配);JSON 列存标量字符串
            "routing_rules": scope.expr,
            "updated_at": _utcnow(),
        }
        existing = await self._db.get(ROUTING_SCOPE_TABLE, {"scope_id": scope.scope_id})
        if existing is not None:
            await self._db.update(ROUTING_SCOPE_TABLE, {"scope_id": scope.scope_id}, row)
        else:
            row["created_at"] = _utcnow()
            await self._db.create(ROUTING_SCOPE_TABLE, row)

    # ---- 变更扩散辅助（沿用 M 期机制）

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
