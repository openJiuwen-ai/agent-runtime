"""Manager instance resources -> Agent Runtime template/scope full sync."""
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
from manager_server.models.instance_resource_models import INSTANCE_SERVICE_RESOURCE_TABLE_DEF
from manager_server.models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF

_log = get_logger(__name__)
_CAP = 100_000


def _g(row: Any, key: str, default: Any = None) -> Any:
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


def _match_expr_to_runtime_rules(value: Any) -> list[dict[str, Any]]:
    if value in (None, [], ""):
        return []
    texts = value if isinstance(value, list) else [value]
    rules: list[dict[str, Any]] = []
    for text in texts:
        rules.extend(_node_to_rules(ast.parse(str(text), mode="eval").body))
    return rules


def service_template_payload(row: Any) -> dict[str, Any]:
    data = _g(row, "data") if isinstance(_g(row, "data"), dict) else {}
    return {
        "template_id": str(_g(row, "template_id")),
        "template_name": str(_g(row, "template_name") or ""),
        "description": str(_g(row, "description") or ""),
        "agent_image": str(_g(row, "agent_image") or ""),
        "namespace": str(_g(row, "namespace") or "default"),
        "pod_name": str(_g(row, "pod_name") or "agentserver"),
        "container_name": str(_g(row, "container_name") or "agent"),
        "container_port": int(_g(row, "container_port") or 8080),
        "sse_port": int(data.get("sse_port") or _g(row, "container_port") or 8080),
        "sse_path": str(data.get("sse_path") or "/api/v1/events/stream"),
        "health_path": str(data.get("health_path") or "/api/v1/health"),
        "agent_env": data.get("agent_env") if isinstance(data.get("agent_env"), dict) else {},
        "image_pull_policy": str(_g(row, "image_pull_policy") or "IfNotPresent"),
        "scope_concurrency": int(_g(row, "session_concurrency") or 3),
        "pod_concurrency": int(_g(row, "service_concurrency") or 2),
        "session_ttl": int(_g(row, "session_ttl") or 60),
        "pod_ttl": int(_g(row, "service_ttl") or 300),
        "min_idle_pods": int(_g(row, "min_idle_services") or 0),
        "readiness_initial_delay": int(_g(row, "readiness_initial_delay") or 5),
        "readiness_period": int(_g(row, "readiness_period") or 5),
        "ready_timeout": int(_g(row, "ready_timeout") or 300),
        "ready_poll_interval": int(_g(row, "ready_poll_interval") or 2),
        "nfs_server": _g(row, "nfs_server"), "nfs_path": _g(row, "nfs_path"),
        "nfs_mount_path": _g(row, "nfs_mount_path"), "kubeconfig": _g(row, "kubeconfig"),
        "agent_cpu_request": _g(row, "agent_cpu_request"),
        "agent_memory_request": _g(row, "agent_memory_request"),
        "agent_cpu_limit": _g(row, "agent_cpu_limit"),
        "agent_memory_limit": _g(row, "agent_memory_limit"),
        "message_timeout": int(_g(row, "message_timeout") or 600),
        "enabled": bool(_g(row, "enabled", True)), "data": data,
    }


async def build_runtime_config(handler: DBHandler, jiuwenclaw_id: str) -> dict[str, list[dict[str, Any]]]:
    """将实例 Service Resource 全量投影为 Runtime templates/scopes。"""
    resources = await handler.list_records(
        INSTANCE_SERVICE_RESOURCE_TABLE_DEF.table_name,
        {"jiuwenclaw_id": jiuwenclaw_id}, limit=_CAP, offset=0,
    )
    templates: dict[str, dict[str, Any]] = {}
    scopes: list[dict[str, Any]] = []
    grouped: dict[str, list[Any]] = {}
    for resource in resources:
        rid = str(_g(resource, "resource_id") or "").strip()
        if rid:
            grouped.setdefault(rid, []).append(resource)

    for rid, grants in grouped.items():
        active = [row for row in grants if bool(_g(row, "enabled", True)) and not _is_expired(_g(row, "expires_at"))]
        if not active:
            continue
        primary = max(active, key=lambda row: int(_g(row, "priority", 0) or 0))
        service_id = str(_g(primary, "ref_template_id") or "").strip()
        service = await handler.get(
            SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name, {"template_id": service_id}
        ) if service_id else None
        if service is None or not bool(_g(service, "enabled", True)):
            _log.warning("runtime sync skipped resource without enabled service template: %s", rid)
            continue
        templates[service_id] = service_template_payload(service)
        rules: list[dict[str, Any]] = []
        for row in active:
            rules.extend(_match_expr_to_runtime_rules(_g(row, "match_expr")))
        scopes.append({
            "scope_id": _scope_id(rid),
            "index": -int(_g(primary, "priority", 0) or 0),
            "template_id": service_id,
            "routing_rules": rules,
        })
    return {"templates": list(templates.values()), "scopes": scopes}


async def sync_runtime_config(handler: DBHandler, jiuwenclaw_id: str) -> dict[str, Any]:
    endpoint = settings.agent_runtime_endpoint.strip().rstrip("/")
    if not endpoint:
        _log.info("AGENT_RUNTIME_ENDPOINT not configured; runtime sync skipped")
        return {"skipped": True}
    rawdata = await build_runtime_config(handler, jiuwenclaw_id)
    envelope = {"type": "config_sync", "metadata": {
        "request_id": f"manager-{uuid.uuid4().hex}", "session_id": None,
        "user_id": "manager", "bot_id": "manager", "extra": {"group_id": jiuwenclaw_id},
    }, "rawdata": rawdata}
    async with httpx.AsyncClient(timeout=settings.agent_runtime_sync_timeout) as client:
        response = await client.post(f"{endpoint}/api/session/config_sync", json=envelope)
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict) and body.get("ok") is False:
        raise ValueError(body.get("error_message") or "agent runtime config sync failed")
    _log.info("runtime config synced", jiuwenclaw_id=jiuwenclaw_id,
              templates=len(rawdata["templates"]), scopes=len(rawdata["scopes"]))
    return body if isinstance(body, dict) else {"ok": True}
