"""Manager instance resources -> Agent Runtime template/scope full sync.

对齐 Runtime ``config_sync`` 三段式独占契约
``{containers, templates, scopes}``：
- 模板只持引用键 + 模板级字段（禁止与内联容器键 mixed）；
- ``nodeName`` 用 K8s wire 拼写；
- ``routing_rules`` 为布尔表达式字符串（非结构化 list）。
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.service_config_container import load_wires_for_template
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.utils import iso_datetime
from manager_server.manager_config_push.client import runtime_request
from manager_server.models.instance_resource_models import INSTANCE_SERVICE_RESOURCE_TABLE_DEF
from manager_server.models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF

_log = get_logger(__name__)
_CAP = 100_000

# 与 Runtime TEMPLATE_LEVEL_FIELDS + 引用键对齐（不含内联容器列）
_TEMPLATE_WIRE_KEYS = (
    "template_id",
    "template_name",
    "description",
    "enabled",
    "data",
    "namespace",
    "pod_name",
    "sse_path",
    "ready_timeout",
    "ready_poll_interval",
    "kubeconfig",
    "scope_concurrency",
    "pod_concurrency",
    "session_ttl",
    "pod_ttl",
    "min_idle_pods",
    "message_timeout",
)


def _g(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


_SCOPE_SAFE = re.compile(r"[^0-9A-Za-z._-]+")


def _is_expired(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return False
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value <= datetime.now(UTC)


def _scope_id(resource_id: str) -> str:
    safe = _SCOPE_SAFE.sub("-", resource_id).strip("-")
    if not safe:
        safe = uuid.uuid5(uuid.NAMESPACE_URL, resource_id).hex
    return f"service-{safe}"[:128]


def _comparison_to_expression(node: ast.Compare) -> dict[str, Any]:
    if len(node.ops) != 1 or len(node.comparators) != 1 or not isinstance(node.left, ast.Name):
        raise ValueError("runtime scope only supports a single comparison")
    field = node.left.id
    if field not in {"user_id", "group_id", "bot_id"}:
        raise ValueError(f"unsupported runtime scope field: {field}")
    op_node = node.ops[0]
    raw = ast.literal_eval(node.comparators[0])
    values = list(raw) if isinstance(raw, (list, tuple, set)) else [raw]
    if not values or any(not isinstance(value, str) for value in values):
        raise ValueError("runtime scope values must be non-empty strings")
    if isinstance(op_node, (ast.Eq, ast.In)):
        op = "in"
    elif isinstance(op_node, (ast.NotEq, ast.NotIn)):
        op = "not_in"
    else:
        raise TypeError("unsupported runtime scope operator")
    return {"field": field, "op": op, "values": values}


def _node_to_rules(node: ast.AST) -> list[dict[str, Any]]:
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return [rule for value in node.values for rule in _node_to_rules(value)]
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        expressions: list[dict[str, Any]] = []
        for value in node.values:
            nested = _node_to_rules(value)
            if len(nested) != 1:
                raise ValueError("nested OR inside AND is not supported")
            expressions.extend(nested[0]["expressions"])
        return [{"expressions": expressions}]
    if isinstance(node, ast.Compare):
        return [{"expressions": [_comparison_to_expression(node)]}]
    raise ValueError("unsupported runtime scope match expression")


def _match_expr_to_rule_groups(value: Any) -> list[dict[str, Any]]:
    if value in (None, [], ""):
        return []
    texts = value if isinstance(value, list) else [value]
    rules: list[dict[str, Any]] = []
    for text in texts:
        rules.extend(_node_to_rules(ast.parse(str(text), mode="eval").body))
    return rules


def _escape_expr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _expr_atom(field: str, op: str, values: list[str]) -> str:
    joined = ", ".join(f"'{_escape_expr_value(v)}'" for v in values)
    if op == "not_in":
        return f"{field} not in ({joined})"
    return f"{field} in ({joined})"


def rule_groups_to_routing_rules(rules: list[dict[str, Any]]) -> str:
    """结构化 match 组 → Runtime routing_rules 表达式字符串。

    组内 expressions 为 and，组间为 or；空 → 通配空串。
    多组 or 时，含 and 的组加括号保证优先级。
    """
    groups: list[str] = []
    group_needs_paren: list[bool] = []
    for rule in rules:
        exprs = rule.get("expressions") if isinstance(rule, dict) else None
        if not isinstance(exprs, list) or not exprs:
            continue
        parts: list[str] = []
        for item in exprs:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            op = str(item.get("op") or "in")
            values = item.get("values")
            if not field or not isinstance(values, list) or not values:
                continue
            parts.append(_expr_atom(field, op, [str(v) for v in values]))
        if not parts:
            continue
        groups.append(" and ".join(parts))
        group_needs_paren.append(len(parts) > 1)
    if not groups:
        return ""
    if len(groups) == 1:
        return groups[0]
    rendered: list[str] = []
    for text, need_paren in zip(groups, group_needs_paren, strict=True):
        rendered.append(f"({text})" if need_paren else text)
    return " or ".join(rendered)


async def service_template_wire(
    handler: DBHandler, row: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """模板行 → (split 形态 template wire, containers)。

    容器从 ``service_config_container`` 表加载（存量可回退 data.config_sync）。
    必须具备 main_container_id 且在 loaded containers 中可解析；
    缺 sidecar / 缺主容器 / 空 image 时返回 None（调用方跳过并告警）。
    """
    tid = str(_g(row, "template_id") or "")
    if not tid:
        return None

    containers = await load_wires_for_template(handler, row)
    by_id = {str(c["container_id"]): c for c in containers}

    main_cid = _g(row, "main_container_id")
    sidecar_ids = _g(row, "sidecar_container_ids")
    if not isinstance(sidecar_ids, list):
        sidecar_ids = []
    sidecar_ids = [str(x) for x in sidecar_ids if isinstance(x, str) and x.strip()]

    if not (isinstance(main_cid, str) and main_cid.strip()):
        _log.warning(
            "runtime sync skip template %s: missing main_container_id",
            tid,
        )
        return None
    main_cid = main_cid.strip()

    if main_cid not in by_id:
        _log.warning(
            "runtime sync skip template %s: main container %s not in container table "
            "(save full containers via Manager edit page / import)",
            tid,
            main_cid,
        )
        return None

    missing_sidecars = [cid for cid in sidecar_ids if cid not in by_id]
    if missing_sidecars:
        _log.warning(
            "runtime sync skip template %s: missing sidecar containers %s "
            "(save full containers via Manager edit page / import)",
            tid,
            missing_sidecars,
        )
        return None

    if not str(by_id[main_cid].get("image") or "").strip():
        _log.warning("runtime sync skip template %s: empty main container image", tid)
        return None

    used_containers: list[dict[str, Any]] = []
    for cid in [main_cid, *sidecar_ids]:
        container = dict(by_id[cid])
        if cid == main_cid and isinstance(container.get("readinessProbe"), dict):
            # Runtime derives the main-container probe timeout and rejects this
            # otherwise valid catalog field. Keep the stored template unchanged
            # so the same container can still be used as a sidecar elsewhere.
            probe = dict(container["readinessProbe"])
            probe.pop("timeoutSeconds", None)
            container["readinessProbe"] = probe
        used_containers.append(container)

    data = _g(row, "data") if isinstance(_g(row, "data"), dict) else {}
    wire: dict[str, Any] = {
        "template_id": tid,
        "template_name": str(_g(row, "template_name") or ""),
        "description": str(_g(row, "description") or ""),
        "enabled": bool(_g(row, "enabled", True)),
        "data": data,
        # ns 不再由管理面配置(Web UI 入口已删):恒发空串 = 继承,
        # AgentServer 跟随 runtime 自身 ns(Runtime 侧 POD_NAMESPACE 兜底链;
        # 行内存量值不透传)
        "namespace": "",
        "pod_name": str(_g(row, "pod_name") or "agentserver"),
        "sse_path": str(_g(row, "sse_path") or data.get("sse_path") or "/api/v1/events/stream"),
        "ready_timeout": int(_g(row, "ready_timeout") or 300),
        "ready_poll_interval": int(_g(row, "ready_poll_interval") or 2),
        "scope_concurrency": int(_g(row, "scope_concurrency") or 3),
        "pod_concurrency": int(_g(row, "pod_concurrency") or 2),
        "session_ttl": int(_g(row, "session_ttl") or 60),
        "pod_ttl": int(_g(row, "pod_ttl") or 300),
        "min_idle_pods": int(_g(row, "min_idle_pods") or 0),
        "message_timeout": int(_g(row, "message_timeout") or 600),
        "main_container_id": main_cid,
    }
    node_name = _g(row, "node_name")
    if isinstance(node_name, str) and node_name.strip():
        wire["nodeName"] = node_name.strip()
    fs_group = _g(row, "fs_group")
    if fs_group is not None:
        wire["fsGroup"] = int(fs_group)
    kubeconfig = _g(row, "kubeconfig")
    if kubeconfig:
        wire["kubeconfig"] = kubeconfig
    if sidecar_ids:
        wire["sidecar_container_ids"] = sidecar_ids
    volumes = _g(row, "volumes")
    if isinstance(volumes, list) and volumes:
        wire["volumes"] = volumes

    # 仅保留白名单键，杜绝 mixed
    allowed = set(_TEMPLATE_WIRE_KEYS) | {
        "main_container_id",
        "sidecar_container_ids",
        "volumes",
        "nodeName",
        "fsGroup",
    }
    wire = {k: v for k, v in wire.items() if k in allowed}
    return wire, used_containers


async def build_runtime_config(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    resource_rows: list[Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """将实例 Service Resource 全量投影为 Runtime ``{containers, templates, scopes}``。

    ``resource_rows`` 可传入尚未落库的投影行，用于「先 sync 再写库」。
    """
    if resource_rows is None:
        resources = await handler.list_records(
            INSTANCE_SERVICE_RESOURCE_TABLE_DEF.table_name,
            {"jiuwenclaw_id": jiuwenclaw_id},
            limit=_CAP,
            offset=0,
        )
    else:
        resources = resource_rows

    templates: dict[str, dict[str, Any]] = {}
    containers_by_id: dict[str, dict[str, Any]] = {}
    scopes: list[dict[str, Any]] = []

    for resource in resources:
        rid = str(_g(resource, "resource_id") or "").strip()
        if not rid:
            continue
        if not bool(_g(resource, "enabled", True)) or _is_expired(_g(resource, "expires_at")):
            continue

        service_id = str(_g(resource, "ref_template_id") or "").strip()
        service = (
            await handler.get(
                SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name, {"template_id": service_id}
            )
            if service_id
            else None
        )
        if service is None or not bool(_g(service, "enabled", True)):
            _log.warning("runtime sync skipped resource without enabled service template: %s", rid)
            continue

        wired = await service_template_wire(handler, service)
        if wired is None:
            _log.warning(
                "runtime sync skipped resource %s: template %s cannot form split payload",
                rid,
                service_id,
            )
            continue
        tpl_wire, tpl_containers = wired
        templates[service_id] = tpl_wire
        for container in tpl_containers:
            cid = str(container.get("container_id") or "")
            if cid:
                containers_by_id[cid] = container

        scopes.append(
            {
                "scope_id": _scope_id(rid),
                "index": int(_g(resource, "priority", 0) or 0),
                "template_id": service_id,
                "routing_rules": rule_groups_to_routing_rules(
                    _match_expr_to_rule_groups(_g(resource, "match_expr"))
                ),
                "enabled": bool(_g(resource, "enabled", True)),
                "expires_at": iso_datetime(_g(resource, "expires_at")),
                "data": (
                    _g(resource, "data")
                    if isinstance(_g(resource, "data"), dict)
                    else None
                ),
            }
        )

    return {
        "containers": list(containers_by_id.values()),
        "templates": list(templates.values()),
        "scopes": scopes,
    }


async def sync_runtime_config(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    resource_rows: list[Any] | None = None,
) -> dict[str, Any]:
    """向对应实例的 Runtime 全量同步 Service Resource 投影。

    目标地址取自该实例 ``runtime_host``（与 Gateway 下发对称）。
    调用方应在 Manager 落库前传入 ``resource_rows``（目标态），避免 Manager/Runtime 不一致。
    无 runtime endpoint 时跳过（返回 ``{skipped: True}``）；其它 HTTP 错误上抛。
    """
    rawdata = await build_runtime_config(handler, jiuwenclaw_id, resource_rows=resource_rows)
    envelope = {
        "type": "config_sync",
        "metadata": {
            "request_id": f"manager-{uuid.uuid4().hex}",
            "session_id": None,
            "user_id": "manager",
            "bot_id": "manager",
            "extra": {"group_id": jiuwenclaw_id},
        },
        "rawdata": rawdata,
    }
    try:
        body = await runtime_request(
            jiuwenclaw_id,
            "POST",
            "/api/session/config_sync",
            envelope,
            handler=handler,
        )
    except ValueError as exc:
        msg = str(exc)
        if "no runtime_host" in msg or "instance not found" in msg:
            _log.info(
                "no runtime endpoint for jiuwenclaw_id=%s; runtime sync skipped",
                jiuwenclaw_id,
            )
            return {"skipped": True}
        raise
    _log.info(
        "runtime config synced",
        jiuwenclaw_id=jiuwenclaw_id,
        containers=len(rawdata["containers"]),
        templates=len(rawdata["templates"]),
        scopes=len(rawdata["scopes"]),
    )
    return body
