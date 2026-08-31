# coding: utf-8
"""Config 层（scope 重构版）：template / routing_scope 持久化 + 快照 + config_sync。

- 存储在共享 DB（config system-of-record），表 ``service_config_template`` /
  ``service_config_container``（容器规格，三段式契约;模板持 main_container_id +
  sidecar_container_ids 引用与 Pod 级 volumes,见 ``container_spec.py``）/
  ``routing_scope``（scope_id / match_index / template_id / routing_rules JSON）；
- ``resolve(user_id, group_id, bot_id)``：route 热路径的路由解析——读 Redis
  单键快照 ``routing:snapshot``（scopes+templates 全量 JSON），进程内按
  (index ASC, scope_id ASC) first-fit 求值；快照缺失/损坏 → 从 DB 重建；
- ``config_sync``：Claw Manager 全量下发入口（场景 M）——``{containers,
  templates, scopes}`` 三段式快照式替换(wire 独占,legacy 内联载荷 400;
  旧 kind/op 增量协议已废弃)。锁内编排：校验 → 日落中间态检查
  （先于写库,拒绝时零副作用）→ 写 DB → 重建快照 → 逐 scope 推 RM 池参数
  （**始终带 pod_spec**——无请求 scope 的 min_idle 预热依赖它）→ A 类软摘除
  老版本 Pod → 被删 scope 推 min_idle=0 自然排空。
  全程持 ``lock:config_sync`` 串行化（忙 → 409 CONFIG_SYNC_BUSY）。

红线：写 DB 失败立即中止，不得 SET 快照、不得推送（防 last-known-good 被污染）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    TableDefinition,
)

from ..errors import ConfigNotFound, ConfigSyncBusy, InvalidParams
from ..sidecars import validate_sidecars
from ..util import key_unsafe, now_ts, s
from .container_spec import (
    MAIN_ROLE,
    SIDECAR_ROLE,
    CONTAINER_TABLE,
    SERVICE_CONFIG_CONTAINER_TABLE_DEF,
    canonical_volumes,
    container_row_from_spec,
    container_spec_from_row,
    main_template_kwargs,
    mounted_volume_names,
    parse_container_spec,
    sidecar_wire_input,
    volumes_from_column,
    volumes_to_column,
)
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
        ColumnDefinition("node_name", "string", length=128, nullable=True),
        ColumnDefinition("run_as_user", "integer", nullable=True),
        ColumnDefinition("run_as_group", "integer", nullable=True),
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
        # 同 Pod sidecar 容器列表(规范形 list[dict];存量库需先手工 ALTER 补列)
        ColumnDefinition("sidecars", "json", nullable=True),
        # 主 agent 容器卷挂载(与 sidecar 挂载同款规范形;存量库同样先 ALTER)
        ColumnDefinition("agent_host_path_mounts", "json", nullable=True),
        ColumnDefinition("agent_configmap_mounts", "json", nullable=True),
        ColumnDefinition("agent_pvc_mounts", "json", nullable=True),
        # 三段式契约(容器表拆分):主容器引用 + sidecar 引用列表 + Pod 级卷定义。
        # 存量库需先手工 ALTER 补列(框架 init_table 只 create_all 不补列)。
        ColumnDefinition("main_container_id", "string", length=100, nullable=True),
        ColumnDefinition("sidecar_container_ids", "json", nullable=True),
        ColumnDefinition("volumes", "json", nullable=True),
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
    "node_name": "node_name",
    "run_as_user": "run_as_user",
    "run_as_group": "run_as_group",
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
    "sidecars": "sidecars",
    "agent_host_path_mounts": "agent_host_path_mounts",
    "agent_configmap_mounts": "agent_configmap_mounts",
    "agent_pvc_mounts": "agent_pvc_mounts",
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
    "run_as_user", "run_as_group",
})

# 模板级字段(留在模板表;容器级 22 字段 + sidecars 由容器表水合,见
# container_spec.main_template_kwargs / sidecar_wire_input)。三段式契约的
# template dict 只认这些键 + main_container_id/sidecar_container_ids/volumes;
# 与 legacy 内联容器键并存 = mixed 形态 → 400。
TEMPLATE_LEVEL_FIELDS: tuple[str, ...] = (
    "template_id", "template_name", "description", "enabled", "data",
    "namespace", "node_name", "pod_name", "sse_path",
    "ready_timeout", "ready_poll_interval", "kubeconfig",
    "scope_concurrency", "pod_concurrency", "session_ttl", "pod_ttl",
    "min_idle_pods", "message_timeout",
)
_SPLIT_REFERENCE_KEYS = frozenset(
    {"main_container_id", "sidecar_container_ids", "volumes"})
# 模板级 wire 键别名:K8s 派生字段用 K8s 拼写(nodeName);snake 双形态拒绝
# (防静默二义——两个拼写同时给不同值无法仲裁,fail-fast)
_TEMPLATE_WIRE_ALIASES = {"node_name": "nodeName"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def template_from_row(row: Any,
                      containers: dict[str, dict[str, Any]] | None = None,
                      ) -> Template | None:
    """DB 行 → Template 业务对象(未命中 enabled=False 的模板仍返回,调用方判定)。

    双形态:行有真值 ``main_container_id`` → 三段式新形态(模板级行列 +
    容器行 + volumes join 水合;任一引用容器行缺失 → WARNING + None,
    绝不静默丢单个 sidecar——那会隐形改 deploy_ver);否则 → legacy 内联
    列路径(旧行,行为逐字节保留,``containers`` 被忽略)。
    """
    main_cid = getattr(row, "main_container_id", None)
    if main_cid:
        return _template_from_split_row(row, main_cid, containers or {})
    kwargs: dict[str, Any] = {}
    for field_name, column in _COLUMN_OF.items():
        value = getattr(row, column, None)
        if field_name in _INT_FIELDS and value is not None:
            value = int(value)
        kwargs[field_name] = value
    # 老行/NULL 防御:agent_env 非 dict → 空表;health_path 空 → 默认;
    # sidecars 坏值/空 → None 的兜底在 Template.__post_init__(normalize_sidecars)
    if not isinstance(kwargs.get("agent_env"), dict):
        kwargs["agent_env"] = {}
    if not kwargs.get("health_path"):
        kwargs["health_path"] = "/health"
    return Template(**kwargs)


def _template_from_split_row(row: Any, main_cid: str,
                             containers: dict[str, dict[str, Any]],
                             ) -> Template | None:
    """新形态行水合:模板级列 + 容器引用 + volumes join(损坏 fail-closed 跳过)。"""
    tid = getattr(row, "template_id", "?")
    main_spec = containers.get(main_cid)
    if main_spec is None:
        logger.warning(
            "template %r references missing main container %r, skipped",
            tid, main_cid,
        )
        return None
    sidecar_ids = getattr(row, "sidecar_container_ids", None) or []
    if not isinstance(sidecar_ids, list):
        sidecar_ids = []
    sidecar_specs = []
    for cid in sidecar_ids:
        spec = containers.get(cid) if isinstance(cid, str) else None
        if spec is None:
            logger.warning(
                "template %r references missing sidecar container %r, skipped",
                tid, cid,
            )
            return None
        sidecar_specs.append(spec)
    volumes = volumes_from_column(getattr(row, "volumes", None))
    kwargs: dict[str, Any] = {}
    for field_name in TEMPLATE_LEVEL_FIELDS:
        column = _COLUMN_OF[field_name]
        value = getattr(row, column, None)
        if field_name in _INT_FIELDS and value is not None:
            value = int(value)
        kwargs[field_name] = value
    if not isinstance(kwargs.get("data"), dict):
        kwargs["data"] = {}
    try:
        kwargs.update(main_template_kwargs(main_spec, volumes, f"template {tid!r}"))
        kwargs["sidecars"] = validate_sidecars(
            [sidecar_wire_input(spec, volumes, f"template {tid!r}")
             for spec in sidecar_specs],
            container_name=str(kwargs.get("container_name") or "agent"),
            sse_port=int(kwargs.get("sse_port") or 8080),
            container_port=int(kwargs.get("container_port") or kwargs.get("sse_port") or 8080),
        )
    except InvalidParams:
        logger.warning(
            "template %r split-form hydration failed, skipped", tid,
            exc_info=True,
        )
        return None
    return Template(**kwargs)


def row_from_template_split(
        t: Template,
        main_container_id: str,
        sidecar_container_ids: tuple[str, ...] | list[str] | None,
        volumes_column: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """三段式新形态模板行:模板级列 + 引用列 + volumes 列。

    容器级 legacy 列不写(NOT NULL 无默认的 agent_image 例外,填 "" 死值,
    create/update 都写——legacy→new 转换不残留旧镜像值误导诊断);
    其余 NOT NULL legacy 列由 ORM 默认兜底成死值。
    """
    row: dict[str, Any] = {"jiuwenclaw_id": TENANT_ID}
    for field_name in TEMPLATE_LEVEL_FIELDS:
        row[_COLUMN_OF[field_name]] = getattr(t, field_name)
    row["agent_image"] = ""
    row["main_container_id"] = main_container_id
    row["sidecar_container_ids"] = (
        list(sidecar_container_ids) if sidecar_container_ids else None)
    row["volumes"] = volumes_column
    return row

def template_from_split_payload(
        template_id: str,
        payload: dict[str, Any],
        containers_by_id: dict[str, dict[str, Any]],
) -> tuple[Template, dict[str, dict[str, Any]]]:
    """三段式 template dict(容器引用形态)→ (Template, 内部卷映射)。

    模板级字段沿用 legacy 同款校验(int 严格/策略下界/node_name hostname);
    主容器/sidecar 规格来自已解析容器规范形(container_spec),挂载经
    volumes join;mixed 形态(引用键与 legacy 内联容器键并存)→ 400。
    返回卷映射供调用方落 volumes 列(volumes_to_column)。
    """
    inline = ({k for k in payload if k in _COLUMN_OF}
              | {k for k in payload if k == "sidecars"}) - set(TEMPLATE_LEVEL_FIELDS)
    if inline:
        raise InvalidParams(
            f"template {template_id!r} mixes container references with inline "
            f"legacy keys {sorted(inline)}; container config belongs in the "
            "containers section"
        )
    defaults = Template(template_id=template_id)
    kwargs: dict[str, Any] = {"template_id": template_id}
    for field in TEMPLATE_LEVEL_FIELDS:
        if field == "template_id":
            continue
        wire_key = _TEMPLATE_WIRE_ALIASES.get(field, field)
        if wire_key != field and payload.get(field) is not None:
            raise InvalidParams(
                f"template {template_id!r}: use the k8s wire spelling "
                f"{wire_key!r} for {field!r} in the split contract")
        if payload.get(wire_key) is not None:
            value = payload[wire_key]
            if field in _INT_FIELDS:
                if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                    raise InvalidParams(
                        f"template field {field} must be an integer, got {value!r}"
                    )
                try:
                    value = int(value)
                except ValueError as exc:
                    raise InvalidParams(
                        f"template field {field} must be an integer, got {value!r}"
                    ) from exc
            kwargs[field] = value
        else:
            kwargs[field] = getattr(defaults, field)
    _validate_policy_fields(template_id, kwargs)
    if kwargs.get("node_name") == "":
        kwargs["node_name"] = None
    _validate_pod_placing_fields(template_id, kwargs)

    main_cid = payload.get("main_container_id")
    if not isinstance(main_cid, str) or not main_cid.strip():
        raise InvalidParams(
            f"template {template_id!r} requires a non-empty main_container_id"
        )
    main_spec = containers_by_id.get(main_cid)
    if main_spec is None:
        raise InvalidParams(
            f"template {template_id!r} main_container_id {main_cid!r} not "
            "present in the containers section"
        )
    volumes = canonical_volumes(
        payload.get("volumes"), f"template {template_id!r}.volumes")

    sidecar_ids = payload.get("sidecar_container_ids")
    if sidecar_ids is None:
        sidecar_ids = []
    if not isinstance(sidecar_ids, list) or any(
            not isinstance(cid, str) for cid in sidecar_ids):
        raise InvalidParams(
            f"template {template_id!r} sidecar_container_ids must be a list "
            f"of container ids, got {sidecar_ids!r}"
        )
    if len(set(sidecar_ids)) != len(sidecar_ids):
        raise InvalidParams(
            f"template {template_id!r} sidecar_container_ids has duplicates: "
            f"{sidecar_ids!r}"
        )
    sidecar_specs = []
    for cid in sidecar_ids:
        spec = containers_by_id.get(cid)
        if spec is None:
            raise InvalidParams(
                f"template {template_id!r} sidecar_container_id {cid!r} not "
                "present in the containers section"
            )
        sidecar_specs.append(spec)

    # 未挂载卷(fail-fast,与未被引用容器对称:全量语义下 = 配置错误)
    mounted = mounted_volume_names(main_spec)
    for spec in sidecar_specs:
        mounted |= mounted_volume_names(spec)
    unused = sorted(set(volumes) - mounted)
    if unused:
        raise InvalidParams(
            f"template {template_id!r} volumes not mounted by any container: "
            f"{unused}"
        )

    where = f"template {template_id!r}"
    kwargs.update(main_template_kwargs(main_spec, volumes, where))
    kwargs["sidecars"] = validate_sidecars(
        [sidecar_wire_input(spec, volumes, where) for spec in sidecar_specs],
        container_name=str(kwargs.get("container_name") or "agent"),
        sse_port=int(kwargs.get("sse_port") or 8080),
        container_port=int(kwargs.get("container_port")
                           or kwargs.get("sse_port") or 8080),
    )
    return Template(**kwargs), volumes


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
        if key_unsafe(getattr(row, "scope_id", "")):
            # scope_id 进多处键名：含 {/} 破坏 hash tag 同槽性，按坏行跳过
            raise ValueError(
                f"scope_id must not contain '{{' or '}}': "
                f"{getattr(row, 'scope_id', None)!r}"
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
# RM 已知 scope 枚举回调：config_sync → rm_facade.known_scope_ids()
# （被删 scope 的 drain 收敛依赖它——DB old_scopes 在删除后即失忆，RM 侧
#  config 键仍在才是「幻影预热还在发生」的真源）
KnownRmScopes = Callable[[], Awaitable[list[str]]]

CONFIG_SYNC_LOCK_TTL = 60  # 串行化锁 TTL（处理超时上限）


@dataclass(frozen=True)
class TemplateSync:
    """一个下发模板的解析产物:业务对象 + 形态附件(legacy 形态附件为 None)。"""
    template: Template
    main_container_id: str | None
    sidecar_container_ids: tuple[str, ...] | None
    volumes_column: list[dict[str, Any]] | None


@dataclass(frozen=True)
class ParsedSync:
    """_parse_payload 产物(锁外校验完成,写库零副作用)。"""
    templates: dict[str, TemplateSync]
    container_specs: dict[str, dict[str, Any]]   # container_id → 内部规范形
    container_rows: dict[str, dict[str, Any]]    # container_id → DB 行列 dict
    scopes: dict[str, RoutingScopeDef]
    wildcard: bool

# 策略字段下界（0/负值 = 拒绝服务配置：pod_concurrency=0 会部满 max_pods 个
# 必然用不上的 Pod 后永久 scope_full）
_POLICY_MINIMUMS = {
    "scope_concurrency": 1,
    "pod_concurrency": 1,
    "session_ttl": 1,
    "pod_ttl": 1,
    "min_idle_pods": 0,
    "ready_timeout": 1,
}


def _validate_policy_fields(template_id: str, kwargs: dict[str, Any]) -> None:
    problems: list[str] = []
    for field, minimum in _POLICY_MINIMUMS.items():
        value = kwargs.get(field)
        if isinstance(value, int) and value < minimum:
            problems.append(f"{field}={value} < {minimum}")
    sse_port = kwargs.get("sse_port")
    if isinstance(sse_port, int) and sse_port and not (1 <= sse_port <= 65535):
        problems.append(f"sse_port={sse_port} out of range")
    if problems:
        raise InvalidParams(
            f"template {template_id!r} policy fields invalid: {'; '.join(problems)}"
        )


# 节点名按宽松 hostname 形态(字母/数字/'-'/'.';K8s 节点名 = DNS subdomain,
# 云厂商 FQDN 形态带 '.',不宜收紧到 DNS label)
_NODE_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _validate_pod_placing_fields(template_id: str, kwargs: dict[str, Any]) -> None:
    """A 类容器身份/节点绑定字段(run_as_user/group、node_name)校验。

    负 uid 与坏节点名都要到 K8s API 侧才失败(后者 Pod 永久 Pending 挂满
    ready_timeout,错误对下发方不可见)——提前到 config_sync 锁外,确定性 400。
    对齐 sidecars.py 对 sidecar run_as_user 的 minimum=0 先例。
    """
    for field in ("run_as_user", "run_as_group"):
        value = kwargs.get(field)
        if isinstance(value, int) and value < 0:
            raise InvalidParams(
                f"template {template_id!r} {field}={value} must be >= 0")
    node_name = kwargs.get("node_name")
    if node_name is not None and (
            not isinstance(node_name, str)
            or len(node_name) > 253
            or not _NODE_NAME_RE.fullmatch(node_name)):
        raise InvalidParams(
            f"template {template_id!r} node_name={node_name!r} must be a "
            "hostname (letters/digits/'-'/'.'; no spaces), <=253 chars")


class ConfigStore:
    """template / routing_scope 的 DB 存取 + 路由快照 + config_sync 编排。"""

    def __init__(
        self,
        db: Any,
        sm_state: SessionState,
        push_pool_config: PoolConfigPush | None = None,
        known_rm_scopes: KnownRmScopes | None = None,
    ) -> None:
        self._db = db
        self.state = sm_state
        self._push = push_pool_config
        self._known_rm_scopes = known_rm_scopes
        # 进程内快照 memo：原始串相等则复用已解析对象（route 热路径零 json.loads）
        self._snapshot_raw: str | None = None
        self._snapshot: RoutingSnapshot | None = None

    # -------------------------------------------------------------- 读路径

    async def get_template(self, template_id: str) -> Template | None:
        """单模板水合(新形态行才取容器表;引用损坏返回 None,日志区分)。"""
        row = await self._db.get(TEMPLATE_TABLE, {"template_id": template_id})
        if row is None:
            return None
        if getattr(row, "main_container_id", None):
            return template_from_row(row, await self._all_containers())
        return template_from_row(row)

    async def list_templates(self, limit: int = 200) -> list[dict[str, Any]]:
        """诊断只读：模板摘要（HLD 字段名；kubeconfig 等敏感列由 /visualization 层脱敏）。"""
        rows = await self._db.list_records(TEMPLATE_TABLE, limit=limit)
        containers = await self._all_containers()
        out: list[dict[str, Any]] = []
        for r in rows:
            t = template_from_row(r, containers)
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
        """全部 scope 定义（快照重建 / /visualization 用；坏行跳过）。"""
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
        """诊断只读（/visualization 用）：当前生效快照（缺失会触发重建）。"""
        return await self._load_snapshot()

    # -------------------------------------------------------------- config_sync

    async def config_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        """处理一次全量配置下发（场景 M）。返回统计 + affected_scopes。"""
        parsed = self._parse_payload(payload)

        token = f"cfgsync-{now_ts()}"
        if not await self.state.try_lock(
            self.state.k.lock_config_sync(), CONFIG_SYNC_LOCK_TTL, token
        ):
            raise ConfigSyncBusy("a previous config_sync is still in progress")
        try:
            return await self._config_sync_locked(parsed)
        finally:
            await self.state.unlock(self.state.k.lock_config_sync(), token)

    @staticmethod
    def _parse_payload(payload: dict[str, Any]) -> ParsedSync:
        """锁外校验（纯 CPU，确定性 400，写库零副作用）。

        只收三段式(wire 独占,2026-08-31 收紧:legacy 内联载荷 → 400——
        契约单一形态,不存在「该发哪种」的歧义;DB 旧行的读兼容水合仍在,
        见 template_from_row)。全部模板必须是引用形态(缺 main_container_id
        / 引用键与内联容器键并存 → 400,防半迁移下发静默生效)。
        """
        if not isinstance(payload, dict):
            raise InvalidParams("config_sync payload must be an object")
        if "kind" in payload or "op" in payload:
            raise InvalidParams(
                "legacy kind/op payload no longer supported; "
                "send {containers: [...], templates: [...], scopes: [...]} "
                "full snapshot"
            )
        if "containers" not in payload:
            raise InvalidParams(
                "config_sync requires the three-part contract "
                "{containers, templates, scopes}; legacy inline templates "
                "are no longer accepted"
            )
        templates_raw = payload.get("templates")
        scopes_raw = payload.get("scopes")
        if not isinstance(templates_raw, list) or not isinstance(scopes_raw, list):
            raise InvalidParams("templates and scopes must both be lists")
        containers_raw = payload.get("containers")

        # ---- 阶段 1:浅扫模板(收引用),不构造
        main_refs: dict[str, str] = {}        # tid → main_container_id
        sidecar_refs: dict[str, list[str]] = {}
        for item in templates_raw:
            if not isinstance(item, dict):
                raise InvalidParams("template item must be an object")
            tid = str(item.get("template_id") or "")
            if not tid:
                raise InvalidParams("template item missing template_id")
            main_cid = item.get("main_container_id")
            if not isinstance(main_cid, str) or not main_cid.strip():
                raise InvalidParams(
                    f"template {tid!r} requires a non-empty main_container_id "
                    "referencing an entry in the containers section"
                )
            main_refs[tid] = main_cid
            sidecar_refs[tid] = list(item.get("sidecar_container_ids") or [])

        # ---- 阶段 2:容器逐项按角色校验(角色 = 引用位置;未引用/双角色 → 400)
        main_ids, sidecar_ids = set(main_refs.values()), set().union(
            *sidecar_refs.values()) if sidecar_refs else set()
        dual = sorted(main_ids & sidecar_ids)
        if dual:
            raise InvalidParams(
                f"containers cannot be both main and sidecar referenced: {dual}"
            )
        referenced = main_ids | sidecar_ids
        container_specs: dict[str, dict[str, Any]] = {}
        container_rows: dict[str, dict[str, Any]] = {}
        if not isinstance(containers_raw, list):
            raise InvalidParams("containers must be a list")
        for i, item in enumerate(containers_raw):
            where = f"containers[{i}]"
            if not isinstance(item, dict):
                raise InvalidParams(f"{where} must be an object")
            cid = item.get("container_id")
            role = MAIN_ROLE if cid in main_ids else SIDECAR_ROLE
            if cid not in referenced:
                raise InvalidParams(
                    f"{where}.container_id {cid!r} is not referenced by any "
                    "template (full-sync semantics: unreferenced config)"
                )
            if cid in container_specs:
                raise InvalidParams(f"duplicate container_id {cid!r}")
            spec = parse_container_spec(item, where, role=role)
            container_specs[cid] = spec
            container_rows[cid] = container_row_from_spec(spec)
        missing = sorted(referenced - set(container_specs))
        if missing:
            raise InvalidParams(
                f"templates reference containers not present in the payload: "
                f"{missing}"
            )

        # ---- 阶段 3:构造模板
        templates_in: dict[str, TemplateSync] = {}
        for item in templates_raw:
            tid = str(item.get("template_id") or "")
            if tid in templates_in:
                raise InvalidParams(f"duplicate template_id {tid!r}")
            template, volumes = template_from_split_payload(
                tid, item, container_specs)
            templates_in[tid] = TemplateSync(
                template=template,
                main_container_id=main_refs[tid],
                sidecar_container_ids=tuple(sidecar_refs[tid]) or None,
                volumes_column=volumes_to_column(volumes),
            )

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
        return ParsedSync(
            templates=templates_in,
            container_specs=container_specs,
            container_rows=container_rows,
            scopes=scopes_in,
            wildcard=wildcard,
        )

    async def _config_sync_locked(self, parsed: ParsedSync) -> dict[str, Any]:
        templates_in = {tid: ts.template for tid, ts in parsed.templates.items()}
        scopes_in = parsed.scopes

        # ---- 旧态（DB system-of-record;新旧形态行统一水合)
        old_containers = await self._all_containers()
        old_templates = {
            t.template_id: t for t in await self._all_templates(old_containers)
        }
        old_scopes = {scope.scope_id: scope for scope in await self.list_scopes()}

        # ---- diff：模板变更集 / 引用切换
        changed_ids = {
            tid for tid, new in templates_in.items()
            if tid not in old_templates
            or self._diff_class(new, old_templates[tid]) != "none"
        }
        ref_switched = {
            sid for sid, scope in scopes_in.items()
            if sid in old_scopes and old_scopes[sid].template_id != scope.template_id
        }
        affected = sorted(
            {sid for sid, scope in scopes_in.items() if scope.template_id in changed_ids}
            | ref_switched
        )

        # ---- 日落中间态检查（★先于写库：拒绝时 DB/Redis 均未动；沿用 M 期语义，
        #      对全部受影响 scope 生效）。判定**按版本**：registered∖candidates
        #      同时是 idle_consider 的合法中间态（HLD §5.1），只有其中
        #      deploy_ver ≠ 新版本的才是真「日落待回收」——按集合形状判定会把
        #      正常空闲 Pod 误当日落遗留，min_idle≥1 时（底数保护永不回收）
        #      变成配置面永久 409。
        for sid in affected:
            new_ver = templates_in[scopes_in[sid].template_id].deploy_ver()
            pending = await self._sunset_pending_pods(sid, new_ver)
            if pending:
                raise ConfigSyncBusy(
                    f"scope {sid} still has sunset pods pending reclaim: {pending}"
                )

        # ---- 写 DB（快照式替换；红线：任一失败立即上抛，不动快照、不推送）。
        #      顺序:先容器后模板(模板引用永不悬挂)→ scopes → GC 容器
        #      (container_id ∉ 本批 → 删;空全量 ⇒ 容器行清空)。
        for cid, row in parsed.container_rows.items():
            await self._upsert_container(cid, row)
        for tid, ts in parsed.templates.items():
            await self._upsert_template_split(tid, ts)
        for tid in set(old_templates) - set(templates_in):
            await self._db.delete(TEMPLATE_TABLE, {"template_id": tid})
        for scope in scopes_in.values():
            await self._upsert_scope(scope)
        for sid in set(old_scopes) - set(scopes_in):
            await self._db.delete(ROUTING_SCOPE_TABLE, {"scope_id": sid})
        containers_deleted = 0
        for cid in set(old_containers) - set(parsed.container_rows):
            if await self._db.delete(CONTAINER_TABLE, {"container_id": cid}):
                containers_deleted += 1

        # ---- 重建快照（DB 读回最终态 → 原子 SET；B 类立即生效由此完成）
        await self.rebuild_snapshot()

        # ---- 扩散①：eager 预热——每个存活 scope 推池参数 + pod_spec（必须带 spec，
        #      RM 才会落 pod_spec_json/deploy_ver，autoscale 才能无请求预热 min_idle）
        for sid, scope in scopes_in.items():
            template = templates_in[scope.template_id]
            await self._push_or_warn(sid, template.pool_config(), template.deploy_subset())

        # ---- 扩散②：候选集版本收敛（声明式，非 one-shot）——对**每个**存活
        #      scope 把 deploy_ver ≠ 当前版本的 Pod ZREM 出候选集（不接新流量；
        #      存量会话亲和不受影响，旧版 idle Pod 由 reclaim 版本感知回收）。
        #      不再由 diff 驱动：写 DB 后中途失败/同载荷重试时 diff==none 会
        #      跳过软摘除，旧版 Pod 将无限期继续接新流量——每拍重算则天然收敛。
        for sid, scope in scopes_in.items():
            new_ver = templates_in[scope.template_id].deploy_ver()
            removed = await self._soft_remove_stale_pods(sid, new_ver)
            if removed:
                logger.info(
                    "config_sync version convergence: scope=%s removed_pods=%s "
                    "new_ver=%s", sid, removed, new_ver,
                )

        # ---- 扩散③：被删 scope → 推 min_idle=0（停预热自然排空；存量会话到期止）。
        #      目标集 = RM 已知 scope ∪ DB 旧 scope − 本批 payload：RM config 键
        #      是幻影预热的真源，DB old_scopes 删行后即失忆——只看 DB 的话，
        #      一次推送失败（滚动重启中断）后该 scope 的 min_idle=0 永远补不上。
        rm_known: set[str] = set()
        if self._known_rm_scopes is not None:
            try:
                rm_known = set(await self._known_rm_scopes())
            except Exception:  # noqa: BLE001 - 枚举失败退回 DB 视图
                logger.exception("known_rm_scopes failed, falling back to db diff")
        for sid in sorted((rm_known | set(old_scopes)) - set(scopes_in)):
            old_tpl = old_templates.get(old_scopes[sid].template_id) if sid in old_scopes else None
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
            "containers_synced": len(parsed.container_rows),
            "containers_deleted": containers_deleted,
            "scopes_synced": len(scopes_in),
            "scopes_deleted": len(set(old_scopes) - set(scopes_in)),
            "affected_scopes": affected,
            "wildcard_present": parsed.wildcard,
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

    # ---- template / container DB 存取

    async def _upsert_template_split(self, template_id: str,
                                     ts: TemplateSync) -> None:
        """三段式形态行(模板级列 + 引用列 + volumes 列;容器行已先行落库)。"""
        row = row_from_template_split(
            ts.template, ts.main_container_id,
            ts.sidecar_container_ids, ts.volumes_column)
        existing = await self._db.get(TEMPLATE_TABLE, {"template_id": template_id})
        if existing is not None:
            row["updated_at"] = _utcnow()
            await self._db.update(TEMPLATE_TABLE, {"template_id": template_id}, row)
        else:
            row["created_at"] = _utcnow()
            row["updated_at"] = _utcnow()
            await self._db.create(TEMPLATE_TABLE, row)

    async def _all_templates(
            self, containers: dict[str, dict[str, Any]] | None = None,
    ) -> list[Template]:
        rows = await self._db.list_records(TEMPLATE_TABLE, limit=10_000)
        if containers is None:
            containers = await self._all_containers()
        out: list[Template] = []
        for r in rows:
            template = template_from_row(r, containers)
            if template is not None:
                out.append(template)
        return out

    async def _all_containers(self, limit: int = 10_000) -> dict[str, dict[str, Any]]:
        """container_id → 内部规范形(水合 join 用;无 JOIN,Python 内字典化)。"""
        rows = await self._db.list_records(CONTAINER_TABLE, limit=limit)
        return {
            spec["container_id"]: spec
            for spec in (container_spec_from_row(r) for r in rows)
            if spec is not None and spec.get("container_id")
        }

    async def _upsert_container(self, container_id: str, row: dict[str, Any]) -> None:
        existing = await self._db.get(CONTAINER_TABLE, {"container_id": container_id})
        if existing is not None:
            row = dict(row)
            row["jiuwenclaw_id"] = TENANT_ID
            row["updated_at"] = _utcnow()
            await self._db.update(CONTAINER_TABLE, {"container_id": container_id}, row)
        else:
            row = dict(row)
            row["jiuwenclaw_id"] = TENANT_ID
            row["created_at"] = _utcnow()
            row["updated_at"] = _utcnow()
            await self._db.create(CONTAINER_TABLE, row)

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

    async def _sunset_pending_pods(self, scope_id: str, new_deploy_ver: str) -> list[str]:
        """日落中间态判定：registered∖candidates 中 deploy_ver ≠ 新版本的 Pod。

        registered∖candidates 也是 idle_consider 的合法中间态（其 Pod 版本
        与当前一致），不计入；版本不同才是「已软摘除、待 reclaim 回收」的
        真日落遗留。info 已缺失的条目（幽灵）无法归因，不计入。
        """
        registered = await self.state.registered_pods()
        prefix = f"{scope_id}:"
        in_candidates = set(await self.state.scope_pod_ids(scope_id))
        pending: list[str] = []
        for entry in registered:
            if not entry.startswith(prefix):
                continue
            pod_id = entry[len(prefix):]
            if pod_id in in_candidates:
                continue
            ver = await self.state.pod_deploy_ver(scope_id, pod_id)
            if ver and ver != new_deploy_ver:
                pending.append(pod_id)
        return pending
