from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from sqlalchemy import insert

from manager_server.core.instance.instance_service import (
    _find_config_host_conflict,
    _find_user_face_host_conflict,
)
from manager_server.core.template.push_template_to_gateway import (
    rebuild_jid_template_ref_for_gateway,
    slot_template_pairs_from_template_ref,
)
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.match_expr import (
    canonicalize_match_expr,
    iter_equality_binds,
)
from manager_server.infrastructure.utils import utc_now
from manager_server.models.application_config_models import (
    _MEMORY_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    AUDIT_LOG_CONFIG_TABLE_DEF,
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
)
from manager_server.models.instance_access_models import INSTANCE_GRANT_TABLE_DEF
from manager_server.models.instance_models import INSTANCE_INFO_TABLE_DEF
from manager_server.models.instance_resource_models import (
    INSTANCE_AGENT_RESOURCE_TABLE_DEF,
    INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
)
from manager_server.models.template_models import (
    A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
    A2A_DISCOVERY_SETTINGS_TABLE_DEF,
    A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
    AGENT_TEMPLATE_TABLE_DEF,
    EMBEDDING_TEMPLATE_TABLE_DEF,
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    MCP_TEMPLATE_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
    PERMISSIONS_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_CONTAINER_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    SKILL_PREBUILT_TEMPLATE_TABLE_DEF,
)

from .registry import ImportExportContext, SheetData, WorkbookData, adapter_registry
from .workbook import SENSITIVE_REDACTED

_AUDIT_FIELDS = {"id", "created_at", "updated_at", "created_by", "updated_by", "granted_by"}
_INSTANCE_RUNTIME_FIELDS = {
    "gateway_status",
    "gateway_last_alive",
    "runtime_status",
    "runtime_last_alive",
    "user_web_status",
    "user_web_last_alive",
}
_A2A_RUNTIME_FIELDS = {
    "pending_revision",
    "last_checked_at",
    "last_error_code",
    "last_error_summary",
    "card_revision",
}
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|password|credential|authorization|cookie|private[_-]?key|kubeconfig)$",
    re.IGNORECASE,
)
_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


@dataclass(frozen=True, slots=True)
class TableSpec:
    sheet: str
    table_def: Any
    unique: tuple[str, ...]
    excluded: frozenset[str] = frozenset()

    @property
    def table(self) -> str:
        return self.table_def.table_name


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "01_Cluster",
        INSTANCE_INFO_TABLE_DEF,
        ("jiuwenclaw_id",),
        frozenset(_INSTANCE_RUNTIME_FIELDS),
    ),
    TableSpec(
        "04_InstanceGrants",
        INSTANCE_GRANT_TABLE_DEF,
        ("jiuwenclaw_id", "subject_type", "subject_id"),
    ),
    TableSpec(
        "05_AgentResources",
        INSTANCE_AGENT_RESOURCE_TABLE_DEF,
        ("resource_id",),
        frozenset({"match_expr"}),
    ),
    TableSpec(
        "06_RuntimeResources",
        INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
        ("resource_id",),
        frozenset({"match_expr"}),
    ),
    TableSpec("08_LogRedactionRules", LOG_MASKING_RULE_TABLE_DEF, ("jiuwenclaw_id", "rule_id")),
    TableSpec(
        "10_AgentTemplates",
        AGENT_TEMPLATE_TABLE_DEF,
        ("template_id",),
        frozenset({"template_ref", "agent_tags"}),
    ),
    TableSpec(
        "12_ModelTemplates",
        MODEL_TEMPLATE_TABLE_DEF,
        ("template_id",),
        frozenset({"model_type", "model_tags"}),
    ),
    TableSpec(
        "13_EmbeddingTemplates",
        EMBEDDING_TEMPLATE_TABLE_DEF,
        ("template_id",),
        frozenset({"embed_tags"}),
    ),
    TableSpec("14_SkillTemplates", SKILL_PREBUILT_TEMPLATE_TABLE_DEF, ("template_id",)),
    TableSpec("15_ExtensionTemplates", EXTENSION_CONFIG_TEMPLATE_TABLE_DEF, ("template_id",)),
    TableSpec("16_MCPTemplates", MCP_TEMPLATE_TABLE_DEF, ("template_id",)),
    TableSpec("17_PermissionTemplates", PERMISSIONS_TEMPLATE_TABLE_DEF, ("template_id",)),
    TableSpec("18_A2ASettings", A2A_DISCOVERY_SETTINGS_TABLE_DEF, ("settings_id",)),
    TableSpec(
        "19_A2AAgents",
        A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
        ("template_id",),
        frozenset(_A2A_RUNTIME_FIELDS | {"a2a_tags"}),
    ),
    TableSpec(
        "20_A2APolicies",
        A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
        ("policy_id",),
        frozenset({"member_template_ids"}),
    ),
    TableSpec(
        "30_RuntimeTemplates",
        SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
        ("template_id",),
        frozenset({"main_container_id", "sidecar_container_ids", "volumes"}),
    ),
    TableSpec(
        "33_ContainerTemplates",
        SERVICE_CONFIG_CONTAINER_TABLE_DEF,
        ("container_id",),
        frozenset({"ports", "env", "env_from", "volume_mounts", "command", "args"}),
    ),
)

SETTINGS_DEFS = (
    LOGGING_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    _MEMORY_CONFIG_TABLE_DEF,
    AUDIT_LOG_CONFIG_TABLE_DEF,
)

SPEC_BY_TABLE = {spec.table: spec for spec in TABLE_SPECS}
SPEC_BY_SHEET = {spec.sheet: spec for spec in TABLE_SPECS}
DEF_BY_TABLE = {spec.table: spec.table_def for spec in TABLE_SPECS}
DEF_BY_TABLE.update({item.table_name: item for item in SETTINGS_DEFS})

_SLOT_TABLE = {
    "default_model": "model_template",
    "video_model": "model_template",
    "audio_model": "model_template",
    "vision_model": "model_template",
    "image_gen_model": "model_template",
    "embedding_model": "embedding_template",
    "skill_prebuilt": "skill_prebuilt_template",
    "extension_config": "extension_config_template",
    "mcp": "mcp_template",
    "permissions": "permissions_template",
    "a2a_access_policy": "a2a_access_policy_template",
}

_IMPORT_ORDER = (
    "a2a_discovery_settings",
    "model_template",
    "embedding_template",
    "skill_prebuilt_template",
    "extension_config_template",
    "mcp_template",
    "permissions_template",
    "a2a_outbound_template",
    "a2a_access_policy_template",
    "service_config_container",
    "agent_template",
    "service_config_template",
    "instance_info",
    "instance_grant",
    "logging_config",
    "task_memory_config",
    "memory_config",
    "audit_log_config",
    "log_masking_rule",
    "instance_agent_resource",
    "instance_service_resource",
)

_SHEET_NOTES = {
    "01_Cluster": "Cluster configuration only; live health status is intentionally omitted.",
    "02_Users": "Directly referenced users only. Fill initial_password only when the target user is missing.",
    "03_Organizations": "Directly referenced organizations only. Membership is intentionally omitted.",
    "90_ExtraFields": "Nested configuration leaves; field_path is stable and machine-readable.",
}

_SHEET_ORDER = (
    "01_Cluster",
    "02_Users",
    "03_Organizations",
    "04_InstanceGrants",
    "05_AgentResources",
    "06_RuntimeResources",
    "07_InstanceSettings",
    "08_LogRedactionRules",
    "10_AgentTemplates",
    "11_AgentTemplateRefs",
    "12_ModelTemplates",
    "13_EmbeddingTemplates",
    "14_SkillTemplates",
    "15_ExtensionTemplates",
    "16_MCPTemplates",
    "17_PermissionTemplates",
    "18_A2ASettings",
    "19_A2AAgents",
    "20_A2APolicies",
    "21_A2APolicyMembers",
    "30_RuntimeTemplates",
    "31_RuntimeContainerBindings",
    "32_RuntimeVolumes",
    "33_ContainerTemplates",
    "34_ContainerDetails",
    "90_ExtraFields",
)

_INLINE_LIST_FIELDS = {
    "instance_agent_resource": ("match_expr",),
    "instance_service_resource": ("match_expr",),
    "agent_template": ("agent_tags",),
    "model_template": ("model_type", "model_tags"),
    "embedding_template": ("embed_tags",),
    "a2a_outbound_template": ("a2a_tags",),
    "service_config_container": ("command", "args"),
}


def _row_dict(row: Any, table_def: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name, None) for column in table_def.columns}


def _columns(table_def: Any, *, excluded: set[str] | frozenset[str] = frozenset()) -> list[Any]:
    return [
        column
        for column in table_def.columns
        if column.name not in _AUDIT_FIELDS and column.name not in excluded
    ]


def _object_id(spec: TableSpec, row: dict[str, Any]) -> str:
    return "|".join(str(row.get(key) or "") for key in spec.unique)


def _sensitive_name(name: str) -> bool:
    return bool(_SENSITIVE_KEY.search(str(name or "").strip()))


def _redact(value: Any, path: str = "", *, parent: dict[str, Any] | None = None) -> Any:
    tail = path.rsplit(".", 1)[-1].split("[", 1)[0]
    if value in (None, ""):
        return value
    if _sensitive_name(tail):
        return SENSITIVE_REDACTED
    if parent and tail == "value" and _sensitive_name(str(parent.get("name") or "")):
        return SENSITIVE_REDACTED
    if isinstance(value, dict):
        return {
            key: _redact(item, f"{path}.{key}" if path else key, parent=value)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, f"{path}[{index}]", parent=None) for index, item in enumerate(value)]
    return value


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    return "string"


def _flatten(value: Any, path: str, out: list[tuple[str, str, Any]]) -> None:
    if isinstance(value, dict):
        if not value:
            out.append((path, "object", ""))
            return
        for key, item in value.items():
            _flatten(item, f"{path}.{key}" if path else str(key), out)
        return
    if isinstance(value, list):
        if not value:
            out.append((path, "array", ""))
            return
        for index, item in enumerate(value):
            _flatten(item, f"{path}[{index}]", out)
        return
    out.append((path, _value_type(value), value))


def _parse_leaf(value_type: str, value: Any) -> Any:
    kind = str(value_type or "string").strip().lower()
    if kind == "null":
        return None
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes"}
    if kind == "number":
        text = str(value or "0").strip()
        return float(text) if any(char in text for char in ".eE") else int(text)
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return "" if value is None else str(value)


def _path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for match in _PATH_TOKEN.finditer(path):
        tokens.append(int(match.group(2)) if match.group(2) is not None else match.group(1))
    return tokens


def _set_path(root: dict[str, Any], path: str, value: Any) -> None:
    tokens = _path_tokens(path)
    if not tokens:
        return
    current: Any = root
    for index, token in enumerate(tokens[:-1]):
        next_token = tokens[index + 1]
        if isinstance(token, str):
            if token not in current or current[token] is None:
                current[token] = [] if isinstance(next_token, int) else {}
            current = current[token]
        else:
            while len(current) <= token:
                current.append(None)
            if current[token] is None:
                current[token] = [] if isinstance(next_token, int) else {}
            current = current[token]
    last = tokens[-1]
    if isinstance(last, str):
        current[last] = value
    else:
        while len(current) <= last:
            current.append(None)
        current[last] = value


def _coerce_scalar(value: Any, column: Any) -> Any:
    if value is None or value == "":
        return None
    kind = str(column.data_type).lower()
    if kind in {"boolean", "bool"}:
        return (
            value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes"}
        )
    if kind in {"integer", "int"}:
        return int(value)
    if kind in {"float", "double", "decimal", "number", "real"}:
        return float(value)
    if kind == "datetime" and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


async def _list(handler: DBHandler, table: str, filters: dict[str, Any]) -> list[Any]:
    return list(await handler.list_records(table, filters, limit=10_000, offset=0))


async def _identity_request(
    method: str, path: str, authorization: str | None, *, body: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    headers = {"Authorization": authorization} if authorization else {}
    base = str(settings.manager_web_idp_target or "").rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.request(method, f"{base}{path}", headers=headers, json=body)
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        detail = response.text[:500]
        raise ValueError(f"identity center request failed ({response.status_code}): {detail}")
    value = response.json()
    return value if isinstance(value, dict) else None


def _referenced_subject_ids(
    rows_by_table: dict[str, list[dict[str, Any]]],
) -> tuple[set[str], set[str]]:
    users: set[str] = set()
    groups: set[str] = set()
    for row in rows_by_table.get("instance_grant", []):
        target = users if row.get("subject_type") == "user" else groups
        target.add(str(row.get("subject_id") or ""))
    for table in ("instance_agent_resource", "instance_service_resource"):
        for row in rows_by_table.get(table, []):
            for name, value in iter_equality_binds(row.get("match_expr")):
                target = users if name == "user_id" else groups
                target.add(value)
    users.discard("")
    groups.discard("")
    return users, groups


async def _collect_rows(handler: DBHandler, jiuwenclaw_id: str) -> dict[str, list[dict[str, Any]]]:
    instance = await handler.get("instance_info", {"jiuwenclaw_id": jiuwenclaw_id})
    if instance is None:
        raise LookupError("cluster not found")
    rows: dict[str, list[dict[str, Any]]] = {
        "instance_info": [_row_dict(instance, INSTANCE_INFO_TABLE_DEF)]
    }
    for table_def in (
        INSTANCE_GRANT_TABLE_DEF,
        INSTANCE_AGENT_RESOURCE_TABLE_DEF,
        INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
        LOG_MASKING_RULE_TABLE_DEF,
        *SETTINGS_DEFS,
    ):
        table_rows = await _list(handler, table_def.table_name, {"jiuwenclaw_id": jiuwenclaw_id})
        rows[table_def.table_name] = [_row_dict(item, table_def) for item in table_rows]

    agent_ids = {str(item.get("ref_template_id")) for item in rows["instance_agent_resource"]}
    runtime_ids = {str(item.get("ref_template_id")) for item in rows["instance_service_resource"]}
    agent_rows = []
    template_ids: dict[str, set[str]] = {}
    for template_id in sorted(agent_ids):
        row = await handler.get("agent_template", {"template_id": template_id})
        if row is None:
            continue
        value = _row_dict(row, AGENT_TEMPLATE_TABLE_DEF)
        agent_rows.append(value)
        for slot, referenced_id in slot_template_pairs_from_template_ref(value.get("template_ref")):
            table = _SLOT_TABLE.get(slot)
            if table:
                template_ids.setdefault(table, set()).add(referenced_id)
    rows["agent_template"] = agent_rows

    for table, ids in template_ids.items():
        table_def = DEF_BY_TABLE[table]
        collected = []
        key = "policy_id" if table == "a2a_access_policy_template" else "template_id"
        for item_id in sorted(ids):
            row = await handler.get(table, {key: item_id})
            if row is not None:
                collected.append(_row_dict(row, table_def))
        rows[table] = collected

    policies = rows.get("a2a_access_policy_template", [])
    if policies:
        policy_member_ids: set[str] = set()
        for row in policies:
            policy_member_ids.update(str(item) for item in (row.get("member_template_ids") or []))
        outbound_by_id: dict[str, dict[str, Any]] = {}
        if any(str(row.get("mode") or "") == "denylist" for row in policies):
            for item in await _list(handler, "a2a_outbound_template", {}):
                value = _row_dict(item, A2A_OUTBOUND_TEMPLATE_TABLE_DEF)
                outbound_by_id[str(value.get("template_id") or "")] = value
        else:
            for template_id in sorted(policy_member_ids):
                row = await handler.get("a2a_outbound_template", {"template_id": template_id})
                if row is not None:
                    outbound_by_id[template_id] = _row_dict(row, A2A_OUTBOUND_TEMPLATE_TABLE_DEF)
        rows["a2a_outbound_template"] = [
            outbound_by_id[key] for key in sorted(outbound_by_id) if key
        ]
        settings_rows = await _list(handler, "a2a_discovery_settings", {})
        rows["a2a_discovery_settings"] = [
            _row_dict(item, A2A_DISCOVERY_SETTINGS_TABLE_DEF) for item in settings_rows
        ]

    runtime_rows = []
    container_ids: set[str] = set()
    for template_id in sorted(runtime_ids):
        row = await handler.get("service_config_template", {"template_id": template_id})
        if row is None:
            continue
        value = _row_dict(row, SERVICE_CONFIG_TEMPLATE_TABLE_DEF)
        runtime_rows.append(value)
        if value.get("main_container_id"):
            container_ids.add(str(value["main_container_id"]))
        container_ids.update(str(item) for item in (value.get("sidecar_container_ids") or []))
    rows["service_config_template"] = runtime_rows
    container_rows = []
    for container_id in sorted(container_ids):
        row = await handler.get("service_config_container", {"container_id": container_id})
        if row is not None:
            container_rows.append(_row_dict(row, SERVICE_CONFIG_CONTAINER_TABLE_DEF))
    rows["service_config_container"] = container_rows
    return rows


def _table_sheet(
    spec: TableSpec, rows: list[dict[str, Any]], extras: list[dict[str, Any]]
) -> SheetData:
    columns = _columns(spec.table_def, excluded=spec.excluded)
    headers = [column.name for column in columns if str(column.data_type).lower() != "json"]
    headers.extend(_INLINE_LIST_FIELDS.get(spec.table, ()))
    if spec.table == "instance_info":
        headers.extend(("gateway_web_http_host", "gateway_web_ws_host"))
    output: list[dict[str, Any]] = []
    sensitive: set[tuple[int, str]] = set()
    for row_index, source in enumerate(rows):
        record: dict[str, Any] = {}
        redacted = _redact(source)
        for column in columns:
            if str(column.data_type).lower() == "json":
                value = redacted.get(column.name)
                if (
                    spec.table == "instance_info"
                    and column.name == "data"
                    and isinstance(value, dict)
                ):
                    value = {
                        key: item
                        for key, item in value.items()
                        if key not in {"gateway_web_http_host", "gateway_web_ws_host"}
                    }
                    # Promoted face-host columns are decoded into ``data``
                    # before ExtraFields.  An empty ``data`` leaf would replace
                    # those hosts again, so omit it from the manifest.
                    if not value:
                        continue
                if value is not None:
                    leaves: list[tuple[str, str, Any]] = []
                    _flatten(value, column.name, leaves)
                    for path, value_type, leaf in leaves:
                        extras.append(
                            {
                                "object_type": spec.table,
                                "object_id": _object_id(spec, source),
                                "field_path": path,
                                "value_type": value_type,
                                "value": leaf,
                            }
                        )
                continue
            value = redacted.get(column.name)
            record[column.name] = value
            if value == SENSITIVE_REDACTED:
                sensitive.add((row_index, column.name))
        for field in _INLINE_LIST_FIELDS.get(spec.table, ()):
            value = redacted.get(field)
            if isinstance(value, list):
                record[field] = "\n".join(str(item) for item in value)
            elif value not in (None, ""):
                record[field] = str(value)
            else:
                record[field] = ""
        if spec.table == "instance_info":
            data = redacted.get("data") if isinstance(redacted.get("data"), dict) else {}
            record["gateway_web_http_host"] = data.get("gateway_web_http_host")
            record["gateway_web_ws_host"] = data.get("gateway_web_ws_host")
        output.append(record)
    return SheetData(
        name=spec.sheet,
        headers=headers,
        rows=output,
        notes=_SHEET_NOTES.get(spec.sheet, f"Manager configuration from {spec.table}."),
        sensitive_cells=sensitive,
    )


def _special_sheets(rows: dict[str, list[dict[str, Any]]]) -> list[SheetData]:
    refs = []
    for agent in rows.get("agent_template", []):
        raw = agent.get("template_ref") or {}
        if not isinstance(raw, dict):
            continue
        for slot, values in raw.items():
            values = values if isinstance(values, list) else [values]
            for index, expression in enumerate(values):
                refs.append(
                    {
                        "agent_template_id": agent.get("template_id"),
                        "slot": slot,
                        "item_index": index,
                        "ref_expression": expression,
                    }
                )
    members = []
    a2a_agent_ids = {
        str(row.get("template_id") or "") for row in rows.get("a2a_outbound_template", [])
    }
    for policy in rows.get("a2a_access_policy_template", []):
        configured_ids = {str(item) for item in (policy.get("member_template_ids") or [])}
        listed_ids = (
            sorted(a2a_agent_ids)
            if str(policy.get("mode") or "") == "denylist"
            else sorted(configured_ids)
        )
        for index, template_id in enumerate(listed_ids):
            configured = template_id in configured_ids
            members.append(
                {
                    "policy_id": policy.get("policy_id"),
                    "member_template_id": template_id,
                    "configured_member": configured,
                    "effective_allow": (
                        configured if policy.get("mode") == "allowlist" else not configured
                    ),
                    "item_index": index,
                }
            )
    bindings = []
    volumes = []
    for runtime in rows.get("service_config_template", []):
        runtime_id = runtime.get("template_id")
        if runtime.get("main_container_id"):
            bindings.append(
                {
                    "runtime_template_id": runtime_id,
                    "role": "main",
                    "item_index": 0,
                    "container_id": runtime.get("main_container_id"),
                }
            )
        for index, container_id in enumerate(runtime.get("sidecar_container_ids") or []):
            bindings.append(
                {
                    "runtime_template_id": runtime_id,
                    "role": "sidecar",
                    "item_index": index,
                    "container_id": container_id,
                }
            )
        redacted_volumes = _redact(runtime.get("volumes") or [], "volumes")
        for index, volume in enumerate(redacted_volumes):
            leaves: list[tuple[str, str, Any]] = []
            _flatten(volume, "", leaves)
            for path, value_type, value in leaves:
                volumes.append(
                    {
                        "runtime_template_id": runtime_id,
                        "item_index": index,
                        "field_path": path,
                        "value_type": value_type,
                        "value": value,
                    }
                )
    details = []
    for container in rows.get("service_config_container", []):
        for detail_type in ("ports", "env", "env_from", "volume_mounts"):
            redacted_details = _redact(container.get(detail_type) or [], detail_type)
            for index, item in enumerate(redacted_details):
                leaves: list[tuple[str, str, Any]] = []
                _flatten(item, "", leaves)
                for path, value_type, value in leaves:
                    details.append(
                        {
                            "container_id": container.get("container_id"),
                            "detail_type": detail_type,
                            "item_index": index,
                            "field_path": path,
                            "value_type": value_type,
                            "value": value,
                        }
                    )
    return [
        SheetData(
            "11_AgentTemplateRefs",
            ["agent_template_id", "slot", "item_index", "ref_expression"],
            refs,
        ),
        SheetData(
            "21_A2APolicyMembers",
            [
                "policy_id",
                "member_template_id",
                "configured_member",
                "effective_allow",
                "item_index",
            ],
            members,
        ),
        SheetData(
            "31_RuntimeContainerBindings",
            ["runtime_template_id", "role", "item_index", "container_id"],
            bindings,
        ),
        SheetData(
            "32_RuntimeVolumes",
            ["runtime_template_id", "item_index", "field_path", "value_type", "value"],
            volumes,
            sensitive_cells={
                (index, "value")
                for index, row in enumerate(volumes)
                if row.get("value") == SENSITIVE_REDACTED
            },
        ),
        SheetData(
            "34_ContainerDetails",
            ["container_id", "detail_type", "item_index", "field_path", "value_type", "value"],
            details,
            sensitive_cells={
                (index, "value")
                for index, row in enumerate(details)
                if row.get("value") == SENSITIVE_REDACTED
            },
        ),
    ]


def _settings_sheet(rows: dict[str, list[dict[str, Any]]]) -> SheetData:
    output: list[dict[str, Any]] = []
    sensitive: set[tuple[int, str]] = set()
    for table_def in SETTINGS_DEFS:
        for source in rows.get(table_def.table_name, []):
            value = {
                key: item
                for key, item in source.items()
                if key not in _AUDIT_FIELDS and key != "jiuwenclaw_id"
            }
            leaves: list[tuple[str, str, Any]] = []
            _flatten(_redact(value), "", leaves)
            for path, value_type, leaf in leaves:
                row_index = len(output)
                output.append(
                    {
                        "jiuwenclaw_id": source.get("jiuwenclaw_id"),
                        "config_domain": table_def.table_name,
                        "field_path": path,
                        "value_type": value_type,
                        "value": leaf,
                    }
                )
                if leaf == SENSITIVE_REDACTED:
                    sensitive.add((row_index, "value"))
    return SheetData(
        "07_InstanceSettings",
        ["jiuwenclaw_id", "config_domain", "field_path", "value_type", "value"],
        output,
        "Instance-level logging, task memory, memory, and audit configuration.",
        sensitive,
    )


def _sheet_map(workbook: WorkbookData) -> dict[str, SheetData]:
    return {sheet.name: sheet for sheet in workbook.sheets}


def _merge_special_rows(
    rows_by_table: dict[str, list[dict[str, Any]]], sheets: dict[str, SheetData]
) -> None:
    by_agent = {str(row.get("template_id")): row for row in rows_by_table.get("agent_template", [])}
    for row in sheets.get("11_AgentTemplateRefs", SheetData("", [])).rows:
        target = by_agent.get(str(row.get("agent_template_id") or ""))
        if target is None:
            continue
        refs = target.setdefault("template_ref", {})
        refs.setdefault(str(row.get("slot") or ""), []).append(str(row.get("ref_expression") or ""))

    by_policy = {
        str(row.get("policy_id")): row
        for row in rows_by_table.get("a2a_access_policy_template", [])
    }
    members: dict[str, list[tuple[int, str]]] = {}
    for row in sheets.get("21_A2APolicyMembers", SheetData("", [])).rows:
        configured = row.get("configured_member")
        if not (
            configured is True or str(configured or "").strip().lower() in {"1", "true", "yes"}
        ):
            continue
        members.setdefault(str(row.get("policy_id") or ""), []).append(
            (int(row.get("item_index") or 0), str(row.get("member_template_id") or ""))
        )
    for policy_id, values in members.items():
        if policy_id in by_policy:
            by_policy[policy_id]["member_template_ids"] = [item for _, item in sorted(values)]

    by_runtime = {
        str(row.get("template_id")): row for row in rows_by_table.get("service_config_template", [])
    }
    for runtime in by_runtime.values():
        # No sidecar binding rows means an explicit empty binding set, which is
        # the canonical form used by the Manager editor.
        runtime.setdefault("sidecar_container_ids", [])
    sidecars: dict[str, list[tuple[int, str]]] = {}
    for row in sheets.get("31_RuntimeContainerBindings", SheetData("", [])).rows:
        runtime_id = str(row.get("runtime_template_id") or "")
        target = by_runtime.get(runtime_id)
        if target is None:
            continue
        if str(row.get("role") or "") == "main":
            target["main_container_id"] = str(row.get("container_id") or "")
        else:
            sidecars.setdefault(runtime_id, []).append(
                (int(row.get("item_index") or 0), str(row.get("container_id") or ""))
            )
    for runtime_id, values in sidecars.items():
        by_runtime[runtime_id]["sidecar_container_ids"] = [item for _, item in sorted(values)]

    volume_roots: dict[tuple[str, int], dict[str, Any]] = {}
    for row in sheets.get("32_RuntimeVolumes", SheetData("", [])).rows:
        key = (str(row.get("runtime_template_id") or ""), int(row.get("item_index") or 0))
        root = volume_roots.setdefault(key, {})
        _set_path(
            root,
            str(row.get("field_path") or ""),
            _parse_leaf(str(row.get("value_type") or ""), row.get("value")),
        )
    for (runtime_id, index), volume in volume_roots.items():
        target = by_runtime.get(runtime_id)
        if target is None:
            continue
        target.setdefault("volumes", [])
        while len(target["volumes"]) <= index:
            target["volumes"].append(None)
        target["volumes"][index] = volume

    by_container = {
        str(row.get("container_id")): row
        for row in rows_by_table.get("service_config_container", [])
    }
    detail_roots: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in sheets.get("34_ContainerDetails", SheetData("", [])).rows:
        key = (
            str(row.get("container_id") or ""),
            str(row.get("detail_type") or ""),
            int(row.get("item_index") or 0),
        )
        root = detail_roots.setdefault(key, {})
        _set_path(
            root,
            str(row.get("field_path") or ""),
            _parse_leaf(str(row.get("value_type") or ""), row.get("value")),
        )
    for (container_id, detail_type, index), detail in detail_roots.items():
        target = by_container.get(container_id)
        if target is None:
            continue
        target.setdefault(detail_type, [])
        while len(target[detail_type]) <= index:
            target[detail_type].append(None)
        target[detail_type][index] = detail


def _decode_workbook(
    workbook: WorkbookData,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    sheets = _sheet_map(workbook)
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for sheet_name, spec in SPEC_BY_SHEET.items():
        sheet = sheets.get(sheet_name)
        if sheet is None:
            raise ValueError(f"missing required sheet: {sheet_name}")
        columns = {
            column.name: column for column in _columns(spec.table_def, excluded=spec.excluded)
        }
        decoded = []
        for raw in sheet.rows:
            value = {
                name: _coerce_scalar(raw.get(name), column)
                for name, column in columns.items()
                if str(column.data_type).lower() != "json" and raw.get(name) not in (None, "")
            }
            for field in _INLINE_LIST_FIELDS.get(spec.table, ()):
                text = str(raw.get(field) or "")
                items = [line.strip() for line in text.splitlines() if line.strip()]
                if items:
                    value[field] = (
                        canonicalize_match_expr(items) if field == "match_expr" else items
                    )
            if spec.table == "instance_info":
                value.setdefault("data", {})
                for field in ("gateway_web_http_host", "gateway_web_ws_host"):
                    if raw.get(field) not in (None, ""):
                        value["data"][field] = str(raw[field])
            decoded.append(value)
        rows_by_table[spec.table] = decoded

    extras = sheets.get("90_ExtraFields")
    if extras is None:
        raise ValueError("missing required sheet: 90_ExtraFields")
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for spec in TABLE_SPECS:
        for row in rows_by_table.get(spec.table, []):
            index[(spec.table, _object_id(spec, row))] = row
    for extra in extras.rows:
        key = (str(extra.get("object_type") or ""), str(extra.get("object_id") or ""))
        target = index.get(key)
        if target is None:
            raise ValueError(f"90_ExtraFields references missing object: {key[0]} / {key[1]}")
        _set_path(
            target,
            str(extra.get("field_path") or ""),
            _parse_leaf(str(extra.get("value_type") or ""), extra.get("value")),
        )
    _merge_special_rows(rows_by_table, sheets)

    settings_sheet = sheets.get("07_InstanceSettings")
    if settings_sheet is None:
        raise ValueError("missing required sheet: 07_InstanceSettings")
    settings_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for item in settings_sheet.rows:
        domain = str(item.get("config_domain") or "")
        if domain not in {table.table_name for table in SETTINGS_DEFS}:
            raise ValueError(f"unknown config_domain in 07_InstanceSettings: {domain}")
        jid = str(item.get("jiuwenclaw_id") or "")
        target = settings_rows.setdefault((domain, jid), {"jiuwenclaw_id": jid})
        _set_path(
            target,
            str(item.get("field_path") or ""),
            _parse_leaf(str(item.get("value_type") or ""), item.get("value")),
        )
    for (domain, _), row in settings_rows.items():
        rows_by_table.setdefault(domain, []).append(row)

    users = sheets.get("02_Users")
    organizations = sheets.get("03_Organizations")
    if users is None or organizations is None:
        raise ValueError("missing required identity sheets: 02_Users / 03_Organizations")
    return rows_by_table, users.rows, organizations.rows


def _without_redacted(value: Any) -> Any:
    if value == SENSITIVE_REDACTED:
        return None
    if isinstance(value, dict):
        return {
            key: _without_redacted(item)
            for key, item in value.items()
            if item != SENSITIVE_REDACTED
        }
    if isinstance(value, list):
        return [_without_redacted(item) for item in value]
    return value


def _matches(existing: Any, desired: Any) -> bool:
    if desired == SENSITIVE_REDACTED:
        return True
    null_matches_empty_list = existing is None and desired == []
    empty_list_matches_null = desired is None and existing == []
    if null_matches_empty_list or empty_list_matches_null:
        return True
    if isinstance(desired, dict):
        if not isinstance(existing, dict):
            return False
        return all(
            key in existing and _matches(existing[key], value) for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(existing, list)
            and len(existing) == len(desired)
            and all(_matches(old, new) for old, new in zip(existing, desired, strict=True))
        )
    if isinstance(desired, datetime):
        old = existing.isoformat() if isinstance(existing, datetime) else str(existing)
        return old == desired.isoformat()
    return existing == desired


def _unique_filters(table: str, row: dict[str, Any]) -> dict[str, Any]:
    spec = SPEC_BY_TABLE.get(table)
    if spec:
        return {key: row.get(key) for key in spec.unique}
    if table in {item.table_name for item in SETTINGS_DEFS}:
        return {"jiuwenclaw_id": row.get("jiuwenclaw_id")}
    raise ValueError(f"no unique key registered for table: {table}")


async def _manager_actions(
    handler: DBHandler, rows_by_table: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for table in _IMPORT_ORDER:
        table_def = DEF_BY_TABLE.get(table)
        if table_def is None:
            continue
        for row in rows_by_table.get(table, []):
            unique = _unique_filters(table, row)
            existing = await handler.get(table, unique)
            object_id = "|".join(str(value or "") for value in unique.values())
            if existing is None:
                status = "create"
                detail = ""
                if table in {"model_template", "embedding_template"} and row.get("api_key") in {
                    None,
                    "",
                    SENSITIVE_REDACTED,
                }:
                    status = "missing_sensitive_value"
                    detail = "api_key must be filled for a new template"
                actions.append(
                    {
                        "scope": "manager",
                        "object_type": table,
                        "object_id": object_id,
                        "action": status,
                        "detail": detail,
                    }
                )
                continue
            existing_value = _row_dict(existing, table_def)
            if _matches(existing_value, row):
                status, detail = "reuse", ""
            else:
                status, detail = "conflict", "same business ID exists with different configuration"
            actions.append(
                {
                    "scope": "manager",
                    "object_type": table,
                    "object_id": object_id,
                    "action": status,
                    "detail": detail,
                }
            )
    return actions


async def _identity_actions(
    authorization: str | None, users: list[dict[str, Any]], organizations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for org in organizations:
        group_id = str(org.get("group_id") or "").strip()
        existing = await _identity_request(
            "GET", f"/v1/orgs/{quote(group_id, safe='')}", authorization
        )
        actions.append(
            {
                "scope": "identity",
                "object_type": "organization",
                "object_id": group_id,
                "action": "reuse" if existing else "create",
                "detail": "existing organization is reused without comparing fields or members"
                if existing
                else "created_without_members",
            }
        )
    for user in users:
        user_id = str(user.get("user_id") or "").strip()
        existing = await _identity_request(
            "GET", f"/v1/users/{quote(user_id, safe='')}", authorization
        )
        action = "reuse" if existing else "create"
        detail = (
            "existing user is reused without overwriting profile or password" if existing else ""
        )
        provider = str(user.get("identity_provider") or "local").strip().lower()
        username = str(user.get("username") or user_id).strip()
        if not existing and provider != "local":
            action = "missing_dependency"
            detail = "federated user must already exist in the target identity center"
        elif not existing:
            username_owner = await _identity_request(
                "GET", f"/v1/users/by-username/{quote(username, safe='')}", authorization
            )
            if username_owner and str(username_owner.get("user_id") or "") != user_id:
                action = "conflict"
                detail = (
                    f"local username is already used by user_id={username_owner.get('user_id')}"
                )
            elif not str(user.get("initial_password") or "").strip():
                action = "missing_sensitive_value"
                detail = "initial_password must be filled for a missing user"
        actions.append(
            {
                "scope": "identity",
                "object_type": "user",
                "object_id": user_id,
                "action": action,
                "detail": detail,
            }
        )
    return actions


async def _identity_dependency_actions(
    authorization: str | None,
    rows: dict[str, list[dict[str, Any]]],
    users: list[dict[str, Any]],
    organizations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect referenced identities omitted from both workbook and target."""
    referenced_users, referenced_groups = _referenced_subject_ids(rows)
    workbook_users = {str(row.get("user_id") or "") for row in users}
    workbook_groups = {str(row.get("group_id") or "") for row in organizations}
    actions: list[dict[str, Any]] = []
    for user_id in sorted(referenced_users - workbook_users):
        existing = await _identity_request(
            "GET", f"/v1/users/{quote(user_id, safe='')}", authorization
        )
        if existing is None:
            actions.append(
                {
                    "scope": "identity",
                    "object_type": "user",
                    "object_id": user_id,
                    "action": "missing_dependency",
                    "detail": "referenced user is absent from 02_Users and target identity center",
                }
            )
    for group_id in sorted(referenced_groups - workbook_groups):
        existing = await _identity_request(
            "GET", f"/v1/orgs/{quote(group_id, safe='')}", authorization
        )
        if existing is None:
            actions.append(
                {
                    "scope": "identity",
                    "object_type": "organization",
                    "object_id": group_id,
                    "action": "missing_dependency",
                    "detail": (
                        "referenced organization is absent from 03_Organizations "
                        "and target identity center"
                    ),
                }
            )
    return actions


def _structural_actions(
    workbook: WorkbookData,
    rows: dict[str, list[dict[str, Any]]],
    users: list[dict[str, Any]],
    organizations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate workbook identity and uniqueness before any target comparison."""
    actions: list[dict[str, Any]] = []
    cluster_rows = rows.get("instance_info", [])
    if len(cluster_rows) != 1:
        actions.append(
            {
                "scope": "workbook",
                "object_type": "instance_info",
                "object_id": workbook.resource_id,
                "action": "conflict",
                "detail": "01_Cluster must contain exactly one data row",
            }
        )
    elif str(cluster_rows[0].get("jiuwenclaw_id") or "") != workbook.resource_id:
        actions.append(
            {
                "scope": "workbook",
                "object_type": "instance_info",
                "object_id": str(cluster_rows[0].get("jiuwenclaw_id") or ""),
                "action": "conflict",
                "detail": "01_Cluster.jiuwenclaw_id differs from 00_ReadMe source_resource_id",
            }
        )

    for table, table_rows in rows.items():
        if table not in DEF_BY_TABLE:
            continue
        seen: set[tuple[tuple[str, str], ...]] = set()
        for index, row in enumerate(table_rows, start=2):
            unique = _unique_filters(table, row)
            missing = [key for key, value in unique.items() if value in (None, "")]
            if missing:
                actions.append(
                    {
                        "scope": "workbook",
                        "object_type": table,
                        "object_id": f"row:{index}",
                        "action": "missing_dependency",
                        "detail": f"missing business key column(s): {', '.join(missing)}",
                    }
                )
                continue
            signature = tuple((key, str(value)) for key, value in unique.items())
            if signature in seen:
                actions.append(
                    {
                        "scope": "workbook",
                        "object_type": table,
                        "object_id": "|".join(value for _, value in signature),
                        "action": "conflict",
                        "detail": f"duplicate business key at decoded row {index}",
                    }
                )
            seen.add(signature)

    for object_type, key, values in (
        ("user", "user_id", users),
        ("organization", "group_id", organizations),
    ):
        seen_ids: set[str] = set()
        for index, row in enumerate(values, start=2):
            object_id = str(row.get(key) or "").strip()
            if not object_id:
                actions.append(
                    {
                        "scope": "workbook",
                        "object_type": object_type,
                        "object_id": f"row:{index}",
                        "action": "missing_dependency",
                        "detail": f"missing business key column: {key}",
                    }
                )
            elif object_id in seen_ids:
                actions.append(
                    {
                        "scope": "workbook",
                        "object_type": object_type,
                        "object_id": object_id,
                        "action": "conflict",
                        "detail": f"duplicate business key at decoded row {index}",
                    }
                )
            seen_ids.add(object_id)
    return actions


async def _a2a_semantic_actions(
    handler: DBHandler, rows: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Prevent denylist imports from silently widening access on the target."""
    deny_policies = [
        row
        for row in rows.get("a2a_access_policy_template", [])
        if str(row.get("mode") or "") == "denylist"
    ]
    if not deny_policies:
        return []
    exported_ids = {
        str(row.get("template_id") or "") for row in rows.get("a2a_outbound_template", [])
    }
    target_ids = {
        str(getattr(row, "template_id", "") or "")
        for row in await _list(handler, "a2a_outbound_template", {})
    }
    extras = sorted((target_ids - exported_ids) - {""})
    if not extras:
        return []
    detail = "target has additional A2A agents that would change denylist semantics: " + ", ".join(
        extras
    )
    return [
        {
            "scope": "manager",
            "object_type": "a2a_access_policy_template",
            "object_id": str(policy.get("policy_id") or ""),
            "action": "conflict",
            "detail": detail,
        }
        for policy in deny_policies
    ]


async def _instance_host_actions(
    handler: DBHandler, rows: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Mirror normal cluster-create host uniqueness checks during preflight."""
    actions: list[dict[str, Any]] = []
    for row in rows.get("instance_info", []):
        cluster_id = str(row.get("jiuwenclaw_id") or "")
        if await handler.get("instance_info", {"jiuwenclaw_id": cluster_id}) is not None:
            continue
        for column in ("gateway_host", "runtime_host", "user_web_host"):
            host = str(row.get(column) or "").strip()
            if not host:
                actions.append(
                    {
                        "scope": "manager",
                        "object_type": "instance_info",
                        "object_id": cluster_id,
                        "action": "missing_dependency",
                        "detail": f"{column} is required",
                    }
                )
                continue
            conflict = await _find_config_host_conflict(handler, host, column=column)
            if conflict is not None:
                actions.append(
                    {
                        "scope": "manager",
                        "object_type": "instance_info",
                        "object_id": cluster_id,
                        "action": "conflict",
                        "detail": (
                            f"{column} is already used by instance "
                            f"{getattr(conflict, 'jiuwenclaw_id', '')}"
                        ),
                    }
                )
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        for key in ("gateway_web_http_host", "gateway_web_ws_host"):
            host = str(data.get(key) or "").strip()
            if key == "gateway_web_http_host" and not host:
                actions.append(
                    {
                        "scope": "manager",
                        "object_type": "instance_info",
                        "object_id": cluster_id,
                        "action": "missing_dependency",
                        "detail": f"{key} is required",
                    }
                )
                continue
            if not host:
                continue
            conflict = await _find_user_face_host_conflict(handler, host, key=key)
            if conflict is not None:
                actions.append(
                    {
                        "scope": "manager",
                        "object_type": "instance_info",
                        "object_id": cluster_id,
                        "action": "conflict",
                        "detail": (
                            f"{key} is already used by instance "
                            f"{getattr(conflict, 'jiuwenclaw_id', '')}"
                        ),
                    }
                )
    return actions


async def _dependency_actions(
    handler: DBHandler, rows: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    desired: dict[str, set[str]] = {}
    for table, values in rows.items():
        for row in values:
            filters = _unique_filters(table, row) if table in DEF_BY_TABLE else {}
            if len(filters) == 1:
                desired.setdefault(table, set()).add(str(next(iter(filters.values())) or ""))

    async def require(table: str, key: str, item_id: str, owner: str) -> None:
        if not item_id or item_id in desired.get(table, set()):
            return
        if await handler.get(table, {key: item_id}) is None:
            actions.append(
                {
                    "scope": "manager",
                    "object_type": owner,
                    "object_id": item_id,
                    "action": "missing_dependency",
                    "detail": f"missing {table}.{key}={item_id}",
                }
            )

    for item in rows.get("instance_agent_resource", []):
        await require(
            "agent_template",
            "template_id",
            str(item.get("ref_template_id") or ""),
            "instance_agent_resource",
        )
    for item in rows.get("instance_service_resource", []):
        await require(
            "service_config_template",
            "template_id",
            str(item.get("ref_template_id") or ""),
            "instance_service_resource",
        )
    for item in rows.get("agent_template", []):
        for slot, template_id in slot_template_pairs_from_template_ref(item.get("template_ref")):
            table = _SLOT_TABLE.get(slot)
            if table:
                key = "policy_id" if table == "a2a_access_policy_template" else "template_id"
                await require(table, key, template_id, "agent_template")
    for item in rows.get("service_config_template", []):
        await require(
            "service_config_container",
            "container_id",
            str(item.get("main_container_id") or ""),
            "service_config_template",
        )
        for container_id in item.get("sidecar_container_ids") or []:
            await require(
                "service_config_container",
                "container_id",
                str(container_id),
                "service_config_template",
            )
    return actions


def _report(actions: list[dict[str, Any]], resource_id: str) -> dict[str, Any]:
    blocking = {"conflict", "missing_dependency", "missing_sensitive_value"}
    counts: dict[str, int] = {}
    for action in actions:
        key = str(action["action"])
        counts[key] = counts.get(key, 0) + 1
    return {
        "resource_type": "cluster",
        "resource_id": resource_id,
        "can_import": not any(action["action"] in blocking for action in actions),
        "summary": counts,
        "actions": actions,
    }


def _prepare_insert(table_def: Any, row: dict[str, Any]) -> dict[str, Any]:
    value = _without_redacted(row)
    now = utc_now()
    names = {column.name for column in table_def.columns}
    if "created_at" in names:
        value.setdefault("created_at", now)
    if "updated_at" in names:
        value.setdefault("updated_at", now)
    if "created_by" in names:
        value.setdefault("created_by", "xlsx-import")
    if "updated_by" in names:
        value.setdefault("updated_by", "xlsx-import")
    return {key: item for key, item in value.items() if key in names and key != "id"}


class ClusterImportExportAdapter:
    resource_type = "cluster"

    async def export(self, context: ImportExportContext, resource_id: str) -> WorkbookData:
        rows = await _collect_rows(context.handler, resource_id)
        users, groups = _referenced_subject_ids(rows)
        user_rows = []
        for user_id in sorted(users):
            user = await _identity_request(
                "GET", f"/v1/users/{quote(user_id, safe='')}", context.authorization
            )
            if user:
                user_rows.append(
                    {
                        "user_id": user_id,
                        "username": user.get("username") or user_id,
                        "identity_provider": user.get("identity_provider") or "federated",
                        "display_name": user.get("display_name") or user_id,
                        "status": user.get("status") or "active",
                        "initial_password": "",
                    }
                )
        org_rows = []
        for group_id in sorted(groups):
            org = await _identity_request(
                "GET", f"/v1/orgs/{quote(group_id, safe='')}", context.authorization
            )
            if org:
                org_rows.append(
                    {
                        "group_id": group_id,
                        "display_name": org.get("display_name") or group_id,
                        "status": org.get("status") or "active",
                    }
                )

        extras: list[dict[str, Any]] = []
        sheets: list[SheetData] = []
        for spec in TABLE_SPECS:
            sheets.append(_table_sheet(spec, rows.get(spec.table, []), extras))
        sheets.extend(_special_sheets(rows))
        sheets.append(_settings_sheet(rows))
        sheets.append(
            SheetData(
                "02_Users",
                [
                    "user_id",
                    "username",
                    "identity_provider",
                    "display_name",
                    "status",
                    "initial_password",
                ],
                user_rows,
                _SHEET_NOTES["02_Users"],
                {(index, "initial_password") for index in range(len(user_rows))},
            )
        )
        sheets.append(
            SheetData(
                "03_Organizations",
                ["group_id", "display_name", "status"],
                org_rows,
                _SHEET_NOTES["03_Organizations"],
            )
        )
        sheets.append(
            SheetData(
                "90_ExtraFields",
                ["object_type", "object_id", "field_path", "value_type", "value"],
                extras,
                _SHEET_NOTES["90_ExtraFields"],
                {
                    (index, "value")
                    for index, row in enumerate(extras)
                    if row.get("value") == SENSITIVE_REDACTED
                },
            )
        )
        order = {name: index for index, name in enumerate(_SHEET_ORDER)}
        sheets.sort(key=lambda sheet: order[sheet.name])
        instance = rows["instance_info"][0]
        return WorkbookData(
            resource_type="cluster",
            resource_id=resource_id,
            resource_name=str(instance.get("jiuwenclaw_name") or resource_id),
            sheets=sheets,
            metadata={"exported_at": datetime.now(UTC).isoformat()},
        )

    async def preflight(
        self, context: ImportExportContext, workbook: WorkbookData
    ) -> dict[str, Any]:
        rows, users, organizations = _decode_workbook(workbook)
        actions = _structural_actions(workbook, rows, users, organizations)
        actions.extend(await _identity_actions(context.authorization, users, organizations))
        actions.extend(
            await _identity_dependency_actions(context.authorization, rows, users, organizations)
        )
        actions.extend(await _manager_actions(context.handler, rows))
        actions.extend(await _instance_host_actions(context.handler, rows))
        actions.extend(await _dependency_actions(context.handler, rows))
        actions.extend(await _a2a_semantic_actions(context.handler, rows))
        return _report(actions, workbook.resource_id)

    async def apply(self, context: ImportExportContext, workbook: WorkbookData) -> dict[str, Any]:
        rows, users, organizations = _decode_workbook(workbook)
        report = await self.preflight(context, workbook)
        if not report["can_import"]:
            raise ValueError("workbook has blocking preflight issues")
        created_identity: list[dict[str, str]] = []
        for org in organizations:
            group_id = str(org.get("group_id") or "").strip()
            existing = await _identity_request(
                "GET", f"/v1/orgs/{quote(group_id, safe='')}", context.authorization
            )
            if existing is None:
                await _identity_request(
                    "POST",
                    "/v1/orgs/",
                    context.authorization,
                    body={
                        "group_id": group_id,
                        "display_name": str(org.get("display_name") or group_id),
                    },
                )
                created_identity.append({"object_type": "organization", "object_id": group_id})
        for user in users:
            user_id = str(user.get("user_id") or "").strip()
            existing = await _identity_request(
                "GET", f"/v1/users/{quote(user_id, safe='')}", context.authorization
            )
            if existing is None:
                await _identity_request(
                    "POST",
                    "/v1/users/",
                    context.authorization,
                    body={
                        "user_id": user_id,
                        "username": str(user.get("username") or user_id),
                        "display_name": str(user.get("display_name") or user_id),
                        "is_admin": False,
                        "password": str(user.get("initial_password") or ""),
                    },
                )
                created_identity.append({"object_type": "user", "object_id": user_id})

        create_rows: list[tuple[str, dict[str, Any]]] = []
        for table in _IMPORT_ORDER:
            table_def = DEF_BY_TABLE.get(table)
            if table_def is None:
                continue
            for row in rows.get(table, []):
                if await context.handler.get(table, _unique_filters(table, row)) is None:
                    create_rows.append((table, _prepare_insert(table_def, row)))

        if isinstance(context.handler, SQLAlchemyHandler) and context.handler.session_factory:
            async with context.handler.session_factory() as session, session.begin():
                for table, row in create_rows:
                    await session.execute(insert(context.handler.get_table(table)).values(**row))
        else:
            for table, row in create_rows:
                await context.handler.create(table, row)

        cluster_ids = {str(row.get("jiuwenclaw_id") or "") for row in rows.get("instance_info", [])}
        created_cluster_ids = {
            str(row.get("jiuwenclaw_id") or "")
            for table, row in create_rows
            if table == "instance_info"
        }
        # New clusters start with pending health state. The existing heartbeat
        # lifecycle performs one full Gateway/Runtime sync on the first online
        # transition; expose that state explicitly to the caller.
        pending_sync: set[str] = set(created_cluster_ids)
        for cluster_id in cluster_ids:
            try:
                await rebuild_jid_template_ref_for_gateway(context.handler, cluster_id)
            except Exception:  # noqa: BLE001 - sync errors are reported for retry, not fatal
                pending_sync.add(cluster_id)
        return {
            "resource_type": "cluster",
            "resource_id": workbook.resource_id,
            "created_manager_objects": len(create_rows),
            "created_identity_objects": created_identity,
            "reused_objects": report["summary"].get("reuse", 0),
            "pending_sync": sorted(pending_sync),
        }


adapter_registry.register(ClusterImportExportAdapter())
