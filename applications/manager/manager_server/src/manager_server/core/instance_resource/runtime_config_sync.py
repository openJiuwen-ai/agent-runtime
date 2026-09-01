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
from datetime import datetime, timezone
from typing import Any

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.config import settings
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.utils import iso_datetime
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
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


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
        raise ValueError("unsupported runtime scope operator")
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


def _env_to_wire(env: Any) -> list[dict[str, str]] | None:
    if isinstance(env, list):
        out: list[dict[str, str]] = []
        for item in env:
            if not isinstance(item, dict) or item.get("name") is None:
                continue
            out.append({"name": str(item["name"]), "value": "" if item.get("value") is None else str(item["value"])})
        return out or None
    if isinstance(env, dict):
        return [{"name": str(k), "value": "" if v is None else str(v)} for k, v in env.items()] or None
    return None


def _synthesize_main_container(row: Any, container_id: str) -> dict[str, Any]:
    """无 data.config_sync.containers 时，由模板内联列合成主容器 wire。"""
    data = _g(row, "data") if isinstance(_g(row, "data"), dict) else {}
    sse_port = int(
        _g(row, "sse_port") or data.get("sse_port") or _g(row, "container_port") or 8080
    )
    health_path = str(
        _g(row, "health_path") or data.get("health_path") or "/api/v1/health"
    )
    wire: dict[str, Any] = {
        "container_id": container_id,
        "name": str(_g(row, "container_name") or "agent"),
        "image": str(_g(row, "agent_image") or ""),
        "imagePullPolicy": str(_g(row, "image_pull_policy") or "IfNotPresent"),
        "ports": [{"name": "sse", "containerPort": sse_port}],
        "readinessProbe": {
            "httpGet": {"path": health_path, "port": sse_port},
            "initialDelaySeconds": int(_g(row, "readiness_initial_delay") or 5),
            "periodSeconds": int(_g(row, "readiness_period") or 5),
        },
    }
    env = _env_to_wire(_g(row, "agent_env") or data.get("agent_env"))
    if env:
        wire["env"] = env
    sc: dict[str, Any] = {}
    if _g(row, "run_as_user") is not None:
        sc["runAsUser"] = int(_g(row, "run_as_user"))
    if _g(row, "run_as_group") is not None:
        sc["runAsGroup"] = int(_g(row, "run_as_group"))
    if sc:
        wire["securityContext"] = sc
    resources: dict[str, Any] = {}
    requests: dict[str, str] = {}
    limits: dict[str, str] = {}
    if _g(row, "agent_cpu_request"):
        requests["cpu"] = str(_g(row, "agent_cpu_request"))
    if _g(row, "agent_memory_request"):
        requests["memory"] = str(_g(row, "agent_memory_request"))
    if _g(row, "agent_cpu_limit"):
        limits["cpu"] = str(_g(row, "agent_cpu_limit"))
    if _g(row, "agent_memory_limit"):
        limits["memory"] = str(_g(row, "agent_memory_limit"))
    if requests:
        resources["requests"] = requests
    if limits:
        resources["limits"] = limits
    if resources:
        wire["resources"] = resources
    return wire


def _stored_containers(row: Any) -> list[dict[str, Any]]:
    data = _g(row, "data")
    if not isinstance(data, dict):
        return []
    sync = data.get("config_sync")
    if not isinstance(sync, dict):
        return []
    containers = sync.get("containers")
    if not isinstance(containers, list):
        return []
    return [c for c in containers if isinstance(c, dict) and c.get("container_id")]


def service_template_wire(row: Any) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """模板行 → (split 形态 template wire, containers)。

    无法凑齐引用容器时返回 None（调用方跳过并告警）。
    """
    tid = str(_g(row, "template_id") or "")
    if not tid:
        return None

    containers = list(_stored_containers(row))
    by_id = {str(c["container_id"]): c for c in containers}

    main_cid = _g(row, "main_container_id")
    sidecar_ids = _g(row, "sidecar_container_ids")
    if not isinstance(sidecar_ids, list):
        sidecar_ids = []
    sidecar_ids = [str(x) for x in sidecar_ids if isinstance(x, str) and x.strip()]

    if not (isinstance(main_cid, str) and main_cid.strip()):
        main_cid = f"c-{tid}-main"
        if main_cid not in by_id:
            by_id[main_cid] = _synthesize_main_container(row, main_cid)
            containers = list(by_id.values())
    elif main_cid not in by_id:
        by_id[main_cid] = _synthesize_main_container(row, main_cid)
        containers = list(by_id.values())

    missing_sidecars = [cid for cid in sidecar_ids if cid not in by_id]
    if missing_sidecars:
        _log.warning(
            "runtime sync skip template %s: missing sidecar containers %s "
            "(save full containers via Manager edit page / import)",
            tid,
            missing_sidecars,
        )
        return None

    if not str(by_id[main_cid].get("image") or "").strip() and not str(
        _g(row, "agent_image") or ""
    ).strip():
        _log.warning("runtime sync skip template %s: empty main container image", tid)
        return None

    # 若合成后 image 仍空，回填行上的 agent_image
    if not str(by_id[main_cid].get("image") or "").strip():
        by_id[main_cid] = {**by_id[main_cid], "image": str(_g(row, "agent_image") or "")}

    referenced = {main_cid, *sidecar_ids}
    used_containers = [by_id[cid] for cid in referenced if cid in by_id]

    data = _g(row, "data") if isinstance(_g(row, "data"), dict) else {}
    wire: dict[str, Any] = {
        "template_id": tid,
        "template_name": str(_g(row, "template_name") or ""),
        "description": str(_g(row, "description") or ""),
        "enabled": bool(_g(row, "enabled", True)),
        "namespace": str(_g(row, "namespace") or "default"),
        "pod_name": str(_g(row, "pod_name") or "agentserver"),
        "sse_path": str(
            _g(row, "sse_path") or data.get("sse_path") or "/api/v1/events/stream"
        ),
        "ready_timeout": int(_g(row, "ready_timeout") or 300),
        "ready_poll_interval": int(_g(row, "ready_poll_interval") or 2),
        "scope_concurrency": int(_g(row, "session_concurrency") or 3),
        "pod_concurrency": int(_g(row, "service_concurrency") or 2),
        "session_ttl": int(_g(row, "session_ttl") or 60),
        "pod_ttl": int(_g(row, "service_ttl") or 300),
        "min_idle_pods": int(_g(row, "min_idle_services") or 0),
        "message_timeout": int(_g(row, "message_timeout") or 600),
        "main_container_id": main_cid,
    }
    node_name = _g(row, "node_name")
    if isinstance(node_name, str) and node_name.strip():
        wire["nodeName"] = node_name.strip()
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
    }
    wire = {k: v for k, v in wire.items() if k in allowed}
    return wire, used_containers


# 兼容旧测试 / 调用方名称
def service_template_payload(row: Any) -> dict[str, Any]:
    """已废弃路径：返回 split 模板 wire（无 containers）。优先用 service_template_wire。"""
    result = service_template_wire(row)
    if result is None:
        return {"template_id": str(_g(row, "template_id") or ""), "enabled": False}
    return result[0]


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
    grouped: dict[str, list[Any]] = {}
    for resource in resources:
        rid = str(_g(resource, "resource_id") or "").strip()
        if rid:
            grouped.setdefault(rid, []).append(resource)

    for rid, grants in grouped.items():
        active = [
            row
            for row in grants
            if bool(_g(row, "enabled", True)) and not _is_expired(_g(row, "expires_at"))
        ]
        if not active:
            continue
        primary = max(active, key=lambda row: int(_g(row, "priority", 0) or 0))
        service_id = str(_g(primary, "ref_template_id") or "").strip()
        service = (
            await handler.get(
                SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name, {"template_id": service_id}
            )
            if service_id
            else None
        )
        if service is None or not bool(_g(service, "enabled", True)):
            _log.warning(
                "runtime sync skipped resource without enabled service template: %s", rid
            )
            continue

        wired = service_template_wire(service)
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

        rule_groups: list[dict[str, Any]] = []
        for row in active:
            rule_groups.extend(_match_expr_to_rule_groups(_g(row, "match_expr")))
        scopes.append(
            {
                "scope_id": _scope_id(rid),
                "index": -int(_g(primary, "priority", 0) or 0),
                "template_id": service_id,
                "routing_rules": rule_groups_to_routing_rules(rule_groups),
                "enabled": bool(_g(primary, "enabled", True)),
                "expires_at": iso_datetime(_g(primary, "expires_at")),
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
    """向 Runtime 全量同步 Service Resource 投影。

    调用方应在 Manager 落库前传入 ``resource_rows``（目标态），避免 Manager/Runtime 不一致。
    """
    endpoint = settings.agent_runtime_endpoint.strip().rstrip("/")
    if not endpoint:
        _log.info("AGENT_RUNTIME_ENDPOINT not configured; runtime sync skipped")
        return {"skipped": True}
    rawdata = await build_runtime_config(
        handler, jiuwenclaw_id, resource_rows=resource_rows
    )
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
    async with httpx.AsyncClient(timeout=settings.agent_runtime_sync_timeout) as client:
        response = await client.post(f"{endpoint}/api/session/config_sync", json=envelope)
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict) and body.get("ok") is False:
        raise ValueError(body.get("error_message") or "agent runtime config sync failed")
    _log.info(
        "runtime config synced",
        jiuwenclaw_id=jiuwenclaw_id,
        containers=len(rawdata["containers"]),
        templates=len(rawdata["templates"]),
        scopes=len(rawdata["scopes"]),
    )
    return body if isinstance(body, dict) else {"ok": True}
