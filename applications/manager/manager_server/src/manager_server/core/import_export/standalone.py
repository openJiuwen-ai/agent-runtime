"""Standalone Manager catalog and identity XLSX import/export adapters.

The sheets intentionally reuse the cluster workbook schema.  Catalog imports
are snapshots: exported business IDs are retained so Agent template references
remain valid after a round trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from sqlalchemy import insert

from manager_server.core.authz import AuthzService
from manager_server.core.template.push_template_to_gateway import (
    slot_template_pairs_from_template_ref,
)
from manager_server.models.authz_models import (
    AUTHZ_PERMISSION_TABLE_DEF,
    AUTHZ_ROLE_PERMISSION_TABLE_DEF,
    AUTHZ_ROLE_TABLE_DEF,
    AUTHZ_ROLE_USER_TABLE_DEF,
)
from manager_server.schemas.template_schemas import (
    A2AAccessPolicyTemplateCreateBody,
    AgentTemplateCreateBody,
    EmbeddingTemplateCreateBody,
    ExtensionConfigTemplateCreateBody,
    McpTemplateCreateBody,
    ModelTemplateCreateBody,
    PermissionsTemplateCreateBody,
    SkillPrebuiltTemplateCreateBody,
)

from .cluster import (
    _INLINE_LIST_FIELDS,
    _SHEET_ORDER,
    _SLOT_TABLE,
    DEF_BY_TABLE,
    SPEC_BY_TABLE,
    _coerce_scalar,
    _columns,
    _dependency_actions,
    _identity_actions,
    _identity_request,
    _list,
    _manager_actions,
    _matches,
    _merge_special_rows,
    _object_id,
    _parse_leaf,
    _prepare_insert,
    _row_dict,
    _set_path,
    _sheet_map,
    _special_sheets,
    _table_sheet,
    _unique_filters,
)
from .registry import ImportExportContext, SheetData, WorkbookData, adapter_registry
from .workbook import SENSITIVE_REDACTED

_EXTRA_HEADERS = ["object_type", "object_id", "field_path", "value_type", "value"]
_USER_HEADERS = [
    "user_id",
    "username",
    "identity_provider",
    "display_name",
    "status",
    "initial_password",
]
_ORG_HEADERS = ["group_id", "display_name", "status"]
_BLOCKING_ACTIONS = {"conflict", "missing_dependency", "missing_sensitive_value"}


@dataclass(frozen=True, slots=True)
class CatalogKind:
    resource_type: str
    primary_table: str
    label: str
    allowed_tables: tuple[str, ...]


_SIMPLE_KINDS = (
    CatalogKind("model", "model_template", "Model", ("model_template",)),
    CatalogKind("embedding", "embedding_template", "EmbeddingModel", ("embedding_template",)),
    CatalogKind("skill", "skill_prebuilt_template", "PrebuiltSkill", ("skill_prebuilt_template",)),
    CatalogKind("guardrail", "permissions_template", "SafetyGuardrail", ("permissions_template",)),
    CatalogKind("extension", "extension_config_template", "ExtensionConfig", ("extension_config_template",)),
    CatalogKind("mcp", "mcp_template", "MCPConfig", ("mcp_template",)),
    CatalogKind("a2a-agent", "a2a_outbound_template", "A2AAgent", ("a2a_outbound_template",)),
)

_AGENT_DEP_TABLES = (
    "model_template",
    "embedding_template",
    "skill_prebuilt_template",
    "extension_config_template",
    "mcp_template",
    "permissions_template",
    "a2a_outbound_template",
    "a2a_access_policy_template",
)

_CREATE_SCHEMAS: dict[str, Any] = {
    "model_template": ModelTemplateCreateBody,
    "embedding_template": EmbeddingTemplateCreateBody,
    "skill_prebuilt_template": SkillPrebuiltTemplateCreateBody,
    "extension_config_template": ExtensionConfigTemplateCreateBody,
    "mcp_template": McpTemplateCreateBody,
    "permissions_template": PermissionsTemplateCreateBody,
    "a2a_access_policy_template": A2AAccessPolicyTemplateCreateBody,
    "agent_template": AgentTemplateCreateBody,
}


def _report(resource_type: str, resource_id: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for action in actions:
        key = str(action["action"])
        counts[key] = counts.get(key, 0) + 1
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "can_import": not any(item["action"] in _BLOCKING_ACTIONS for item in actions),
        "summary": counts,
        "actions": actions,
    }


def _selection_id(resource_ids: list[str]) -> str:
    return resource_ids[0] if len(resource_ids) == 1 else f"selection-{len(resource_ids)}"


def _extra_sheet(extras: list[dict[str, Any]]) -> SheetData:
    return SheetData(
        "90_ExtraFields",
        _EXTRA_HEADERS,
        extras,
        "Nested configuration leaves; field_path is stable and machine-readable.",
        {
            (index, "value")
            for index, row in enumerate(extras)
            if row.get("value") == SENSITIVE_REDACTED
        },
    )


def _sort_sheets(sheets: list[SheetData]) -> list[SheetData]:
    order = {name: index for index, name in enumerate(_SHEET_ORDER)}
    identity_order = {
        "22_OrganizationMembers": 22,
        "40_Roles": 40,
        "41_RolePermissions": 41,
        "42_RoleAssignments": 42,
    }
    return sorted(sheets, key=lambda item: order.get(item.name, identity_order.get(item.name, 999)))


async def _rows_by_ids(
    handler: DBHandler, table: str, resource_ids: list[str]
) -> list[dict[str, Any]]:
    spec = SPEC_BY_TABLE[table]
    if len(spec.unique) != 1:
        raise ValueError(f"collection export is not supported for {table}")
    key = spec.unique[0]
    rows: list[dict[str, Any]] = []
    for resource_id in resource_ids:
        row = await handler.get(table, {key: resource_id})
        if row is None:
            raise LookupError(f"{table} not found: {resource_id}")
        rows.append(_row_dict(row, spec.table_def))
    return rows


def _manager_workbook(
    *,
    resource_type: str,
    resource_ids: list[str],
    label: str,
    rows_by_table: dict[str, list[dict[str, Any]]],
    tables: tuple[str, ...],
) -> WorkbookData:
    extras: list[dict[str, Any]] = []
    sheets = [
        _table_sheet(SPEC_BY_TABLE[table], rows_by_table.get(table, []), extras)
        for table in tables
    ]
    special = {sheet.name: sheet for sheet in _special_sheets(rows_by_table)}
    if "agent_template" in tables:
        sheets.append(special["11_AgentTemplateRefs"])
    if "a2a_access_policy_template" in tables:
        sheets.append(special["21_A2APolicyMembers"])
    sheets.append(_extra_sheet(extras))
    return WorkbookData(
        resource_type=resource_type,
        resource_id=_selection_id(resource_ids),
        resource_name=label if len(resource_ids) == 1 else f"{label}Selection",
        sheets=_sort_sheets(sheets),
        metadata={"exported_at": datetime.now(UTC).isoformat()},
    )


def _decode_manager_workbook(
    workbook: WorkbookData,
    *,
    primary_table: str,
    allowed_tables: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    sheets = _sheet_map(workbook)
    primary_sheet = SPEC_BY_TABLE[primary_table].sheet
    if primary_sheet not in sheets:
        raise ValueError(f"missing required sheet: {primary_sheet}")
    if "90_ExtraFields" not in sheets:
        raise ValueError("missing required sheet: 90_ExtraFields")

    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for table in allowed_tables:
        spec = SPEC_BY_TABLE[table]
        sheet = sheets.get(spec.sheet)
        if sheet is None:
            continue
        columns = {
            column.name: column for column in _columns(spec.table_def, excluded=spec.excluded)
        }
        decoded: list[dict[str, Any]] = []
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
                    value[field] = items
            decoded.append(value)
        rows_by_table[table] = decoded

    index: dict[tuple[str, str], dict[str, Any]] = {}
    for table, rows in rows_by_table.items():
        spec = SPEC_BY_TABLE[table]
        for row in rows:
            index[(table, _object_id(spec, row))] = row
    for extra in sheets["90_ExtraFields"].rows:
        object_type = str(extra.get("object_type") or "")
        if object_type not in allowed_tables:
            raise ValueError(f"90_ExtraFields contains unsupported object_type: {object_type}")
        key = (object_type, str(extra.get("object_id") or ""))
        target = index.get(key)
        if target is None:
            raise ValueError(f"90_ExtraFields references missing object: {key[0]} / {key[1]}")
        _set_path(
            target,
            str(extra.get("field_path") or ""),
            _parse_leaf(str(extra.get("value_type") or ""), extra.get("value")),
        )
    _merge_special_rows(rows_by_table, sheets)
    return rows_by_table


def _catalog_structural_actions(
    rows: dict[str, list[dict[str, Any]]], primary_table: str
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not rows.get(primary_table):
        actions.append({
            "scope": "workbook",
            "object_type": primary_table,
            "object_id": "",
            "action": "missing_dependency",
            "detail": f"{SPEC_BY_TABLE[primary_table].sheet} contains no data rows",
        })
    for table, values in rows.items():
        seen: set[tuple[tuple[str, str], ...]] = set()
        for index, row in enumerate(values, start=2):
            unique = _unique_filters(table, row)
            missing = [key for key, value in unique.items() if value in (None, "")]
            object_id = "|".join(str(value or "") for value in unique.values())
            if missing:
                actions.append({
                    "scope": "workbook", "object_type": table,
                    "object_id": f"row:{index}", "action": "missing_dependency",
                    "detail": f"missing business key column(s): {', '.join(missing)}",
                })
                continue
            signature = tuple((key, str(value)) for key, value in unique.items())
            if signature in seen:
                actions.append({
                    "scope": "workbook", "object_type": table,
                    "object_id": object_id, "action": "conflict",
                    "detail": f"duplicate business key at decoded row {index}",
                })
            seen.add(signature)
    return actions


def _validate_catalog_rows(rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for table, schema in _CREATE_SCHEMAS.items():
        for row in rows.get(table, []):
            fields = set(schema.model_fields)
            body = {key: value for key, value in row.items() if key in fields}
            try:
                schema.model_validate(body)
            except ValueError as exc:  # Pydantic error is intentionally shown in preflight.
                actions.append({
                    "scope": "workbook",
                    "object_type": table,
                    "object_id": "|".join(str(v or "") for v in _unique_filters(table, row).values()),
                    "action": "conflict",
                    "detail": f"invalid configuration: {exc}",
                })
    for row in rows.get("a2a_outbound_template", []):
        required = ("template_name", "source_url", "card_path", "agent_card", "selected_interface")
        missing = [name for name in required if row.get(name) in (None, "")]
        if missing:
            actions.append({
                "scope": "workbook", "object_type": "a2a_outbound_template",
                "object_id": str(row.get("template_id") or ""), "action": "missing_dependency",
                "detail": f"missing required field(s): {', '.join(missing)}",
            })
    return actions


async def _a2a_policy_dependency_actions(
    handler: DBHandler, rows: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    workbook_agent_ids = {
        str(row.get("template_id") or "") for row in rows.get("a2a_outbound_template", [])
    }
    for policy in rows.get("a2a_access_policy_template", []):
        for template_id in policy.get("member_template_ids") or []:
            item_id = str(template_id or "")
            if not item_id or item_id in workbook_agent_ids:
                continue
            if await handler.get("a2a_outbound_template", {"template_id": item_id}) is None:
                actions.append({
                    "scope": "manager",
                    "object_type": "a2a_access_policy_template",
                    "object_id": str(policy.get("policy_id") or ""),
                    "action": "missing_dependency",
                    "detail": f"missing a2a_outbound_template.template_id={item_id}",
                })
    return actions


class StandaloneCatalogImportService:
    """Validated snapshot creator used only by XLSX restore workflows."""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create_missing(self, rows: dict[str, list[dict[str, Any]]]) -> int:
        order = (
            "model_template", "embedding_template", "skill_prebuilt_template",
            "extension_config_template", "mcp_template", "permissions_template",
            "a2a_outbound_template", "a2a_access_policy_template", "agent_template",
        )
        create_rows: list[tuple[str, dict[str, Any]]] = []
        for table in order:
            for row in rows.get(table, []):
                if await self._handler.get(table, _unique_filters(table, row)) is None:
                    create_rows.append((table, _prepare_insert(DEF_BY_TABLE[table], row)))
        if isinstance(self._handler, SQLAlchemyHandler) and self._handler.session_factory:
            async with self._handler.session_factory() as session, session.begin():
                for table, row in create_rows:
                    await session.execute(insert(self._handler.get_table(table)).values(**row))
        else:
            for table, row in create_rows:
                await self._handler.create(table, row)
        return len(create_rows)


class CatalogAdapter:
    def __init__(self, kind: CatalogKind) -> None:
        self.kind = kind
        self.resource_type = kind.resource_type

    async def export(self, context: ImportExportContext, resource_id: str) -> WorkbookData:
        return await self.export_many(context, [resource_id])

    async def export_many(
        self, context: ImportExportContext, resource_ids: list[str]
    ) -> WorkbookData:
        rows = await _rows_by_ids(context.handler, self.kind.primary_table, resource_ids)
        return _manager_workbook(
            resource_type=self.resource_type,
            resource_ids=resource_ids,
            label=self.kind.label,
            rows_by_table={self.kind.primary_table: rows},
            tables=self.kind.allowed_tables,
        )

    async def preflight(
        self, context: ImportExportContext, workbook: WorkbookData
    ) -> dict[str, Any]:
        rows = _decode_manager_workbook(
            workbook,
            primary_table=self.kind.primary_table,
            allowed_tables=self.kind.allowed_tables,
        )
        actions = _catalog_structural_actions(rows, self.kind.primary_table)
        actions.extend(_validate_catalog_rows(rows))
        actions.extend(await _manager_actions(context.handler, rows))
        actions.extend(await _dependency_actions(context.handler, rows))
        actions.extend(await _a2a_policy_dependency_actions(context.handler, rows))
        return _report(self.resource_type, workbook.resource_id, actions)

    async def apply(self, context: ImportExportContext, workbook: WorkbookData) -> dict[str, Any]:
        rows = _decode_manager_workbook(
            workbook,
            primary_table=self.kind.primary_table,
            allowed_tables=self.kind.allowed_tables,
        )
        report = await self.preflight(context, workbook)
        if not report["can_import"]:
            raise ValueError("workbook has blocking preflight issues")
        created = await StandaloneCatalogImportService(context.handler).create_missing(rows)
        return {
            "resource_type": self.resource_type,
            "resource_id": workbook.resource_id,
            "created_manager_objects": created,
            "created_identity_objects": [],
            "reused_objects": report["summary"].get("reuse", 0),
            "pending_sync": [],
        }


class A2APolicyAdapter(CatalogAdapter):
    def __init__(self) -> None:
        super().__init__(CatalogKind(
            "a2a-policy", "a2a_access_policy_template", "A2AAccessPolicy",
            ("a2a_access_policy_template",),
        ))


class AgentTemplateAdapter(CatalogAdapter):
    def __init__(self) -> None:
        super().__init__(CatalogKind(
            "agent-template", "agent_template", "AgentTemplate",
            (*_AGENT_DEP_TABLES, "agent_template"),
        ))

    async def export_many(
        self, context: ImportExportContext, resource_ids: list[str]
    ) -> WorkbookData:
        agent_rows = await _rows_by_ids(context.handler, "agent_template", resource_ids)
        rows: dict[str, list[dict[str, Any]]] = {"agent_template": agent_rows}
        wanted: dict[str, set[str]] = {}
        for agent in agent_rows:
            for slot, item_id in slot_template_pairs_from_template_ref(agent.get("template_ref")):
                table = _SLOT_TABLE.get(slot)
                if table:
                    wanted.setdefault(table, set()).add(item_id)
        for table, ids in wanted.items():
            key = "policy_id" if table == "a2a_access_policy_template" else "template_id"
            values: list[dict[str, Any]] = []
            for item_id in sorted(ids):
                row = await context.handler.get(table, {key: item_id})
                if row is None:
                    continue
                values.append(_row_dict(row, DEF_BY_TABLE[table]))
            rows[table] = values

        policies = rows.get("a2a_access_policy_template", [])
        if policies:
            member_ids: set[str] = set()
            include_all = False
            for policy in policies:
                member_ids.update(str(item) for item in policy.get("member_template_ids") or [])
                include_all = include_all or str(policy.get("mode") or "") == "denylist"
            if include_all:
                outbound = [
                    _row_dict(item, DEF_BY_TABLE["a2a_outbound_template"])
                    for item in await _list(context.handler, "a2a_outbound_template", {})
                ]
            else:
                outbound = []
                for item_id in sorted(member_ids):
                    row = await context.handler.get("a2a_outbound_template", {"template_id": item_id})
                    if row is not None:
                        outbound.append(_row_dict(row, DEF_BY_TABLE["a2a_outbound_template"]))
            rows["a2a_outbound_template"] = outbound

        return _manager_workbook(
            resource_type=self.resource_type,
            resource_ids=resource_ids,
            label=self.kind.label,
            rows_by_table=rows,
            tables=self.kind.allowed_tables,
        )


def _user_row(user: dict[str, Any]) -> dict[str, Any]:
    user_id = str(user.get("user_id") or "")
    return {
        "user_id": user_id,
        "username": user.get("username") or user_id,
        "identity_provider": user.get("identity_provider") or "federated",
        "display_name": user.get("display_name") or user_id,
        "status": user.get("status") or "active",
        "initial_password": "",
    }


async def _fetch_users(
    authorization: str | None, user_ids: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for user_id in user_ids:
        user = await _identity_request(
            "GET", f"/v1/users/{quote(user_id, safe='')}", authorization
        )
        if user is None:
            raise LookupError(f"user not found: {user_id}")
        rows.append(_user_row(user))
    return rows


def _identity_duplicates(
    object_type: str, key: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        object_id = str(row.get(key) or "").strip()
        if not object_id:
            action, detail = "missing_dependency", f"missing business key column: {key}"
        elif object_id in seen:
            action, detail = "conflict", f"duplicate business key at decoded row {index}"
        else:
            seen.add(object_id)
            continue
        actions.append({
            "scope": "workbook", "object_type": object_type,
            "object_id": object_id or f"row:{index}", "action": action, "detail": detail,
        })
    return actions


async def _apply_users(
    context: ImportExportContext, users: list[dict[str, Any]]
) -> list[dict[str, str]]:
    created: list[dict[str, str]] = []
    for user in users:
        user_id = str(user.get("user_id") or "").strip()
        existing = await _identity_request(
            "GET", f"/v1/users/{quote(user_id, safe='')}", context.authorization
        )
        if existing is not None:
            continue
        await _identity_request(
            "POST", "/v1/users/", context.authorization,
            body={
                "user_id": user_id,
                "username": str(user.get("username") or user_id),
                "display_name": str(user.get("display_name") or user_id),
                "is_admin": False,
                "password": str(user.get("initial_password") or ""),
            },
        )
        status = str(user.get("status") or "active")
        if status != "active":
            await _identity_request(
                "PATCH", f"/v1/users/{quote(user_id, safe='')}", context.authorization,
                body={"status": status},
            )
        created.append({"object_type": "user", "object_id": user_id})
    return created


class UserAdapter:
    resource_type = "user"

    async def export(self, context: ImportExportContext, resource_id: str) -> WorkbookData:
        return await self.export_many(context, [resource_id])

    async def export_many(
        self, context: ImportExportContext, resource_ids: list[str]
    ) -> WorkbookData:
        users = await _fetch_users(context.authorization, resource_ids)
        return WorkbookData(
            resource_type=self.resource_type,
            resource_id=_selection_id(resource_ids),
            resource_name="User" if len(resource_ids) == 1 else "UserSelection",
            sheets=[SheetData(
                "02_Users", _USER_HEADERS, users,
                "Selected user profiles. Passwords are never exported; fill initial_password for missing local users.",
                {(index, "initial_password") for index in range(len(users))},
            )],
        )

    def _decode(self, workbook: WorkbookData) -> list[dict[str, Any]]:
        sheet = _sheet_map(workbook).get("02_Users")
        if sheet is None:
            raise ValueError("missing required sheet: 02_Users")
        return sheet.rows

    async def preflight(
        self, context: ImportExportContext, workbook: WorkbookData
    ) -> dict[str, Any]:
        users = self._decode(workbook)
        actions = _identity_duplicates("user", "user_id", users)
        actions.extend(await _identity_actions(context.authorization, users, []))
        return _report(self.resource_type, workbook.resource_id, actions)

    async def apply(self, context: ImportExportContext, workbook: WorkbookData) -> dict[str, Any]:
        users = self._decode(workbook)
        report = await self.preflight(context, workbook)
        if not report["can_import"]:
            raise ValueError("workbook has blocking preflight issues")
        created = await _apply_users(context, users)
        return {
            "resource_type": self.resource_type, "resource_id": workbook.resource_id,
            "created_manager_objects": 0, "created_identity_objects": created,
            "reused_objects": report["summary"].get("reuse", 0), "pending_sync": [],
        }


class OrganizationAdapter:
    resource_type = "organization"

    async def export(self, context: ImportExportContext, resource_id: str) -> WorkbookData:
        return await self.export_many(context, [resource_id])

    async def export_many(
        self, context: ImportExportContext, resource_ids: list[str]
    ) -> WorkbookData:
        orgs: list[dict[str, Any]] = []
        memberships: list[dict[str, Any]] = []
        member_user_ids: set[str] = set()
        for group_id in resource_ids:
            org = await _identity_request(
                "GET", f"/v1/orgs/{quote(group_id, safe='')}", context.authorization
            )
            if org is None:
                raise LookupError(f"organization not found: {group_id}")
            orgs.append({
                "group_id": group_id,
                "display_name": org.get("display_name") or group_id,
                "status": org.get("status") or "active",
            })
            response = await _identity_request(
                "GET", f"/v1/orgs/{quote(group_id, safe='')}/members", context.authorization
            ) or {}
            for member in response.get("users") or []:
                user_id = str(member.get("user_id") or "").strip()
                if not user_id:
                    continue
                member_user_ids.add(user_id)
                memberships.append({"group_id": group_id, "user_id": user_id})
        # The organization-members endpoint intentionally returns a compact user view
        # without the authentication provider. Resolve every member through the user
        # detail endpoint so local users remain creatable after migration instead of
        # being incorrectly exported as federated users.
        users = await _fetch_users(context.authorization, sorted(member_user_ids))
        return WorkbookData(
            resource_type=self.resource_type,
            resource_id=_selection_id(resource_ids),
            resource_name="Organization" if len(resource_ids) == 1 else "OrganizationSelection",
            sheets=[
                SheetData("02_Users", _USER_HEADERS, users,
                          "Referenced member profiles; fill initial_password only for missing local users.",
                          {(index, "initial_password") for index in range(len(users))}),
                SheetData("03_Organizations", _ORG_HEADERS, orgs),
                SheetData("22_OrganizationMembers", ["group_id", "user_id"], memberships),
            ],
        )

    def _decode(
        self, workbook: WorkbookData
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        sheets = _sheet_map(workbook)
        for required in ("02_Users", "03_Organizations", "22_OrganizationMembers"):
            if required not in sheets:
                raise ValueError(f"missing required sheet: {required}")
        return sheets["02_Users"].rows, sheets["03_Organizations"].rows, sheets["22_OrganizationMembers"].rows

    async def preflight(
        self, context: ImportExportContext, workbook: WorkbookData
    ) -> dict[str, Any]:
        users, orgs, memberships = self._decode(workbook)
        actions = _identity_duplicates("user", "user_id", users)
        actions.extend(_identity_duplicates("organization", "group_id", orgs))
        actions.extend(await _identity_actions(context.authorization, [], orgs))
        user_ids = {str(row.get("user_id") or "") for row in users}
        org_ids = {str(row.get("group_id") or "") for row in orgs}
        missing_org_ids: set[str] = set()
        for group_id in org_ids:
            existing = await _identity_request(
                "GET", f"/v1/orgs/{quote(group_id, safe='')}", context.authorization
            )
            if existing is None:
                missing_org_ids.add(group_id)
        required_user_ids = {
            str(row.get("user_id") or "")
            for row in memberships
            if str(row.get("group_id") or "") in missing_org_ids
        }
        required_users = [
            row for row in users if str(row.get("user_id") or "") in required_user_ids
        ]
        actions.extend(await _identity_actions(context.authorization, required_users, []))
        seen: set[tuple[str, str]] = set()
        for row in memberships:
            key = (str(row.get("group_id") or ""), str(row.get("user_id") or ""))
            detail = ""
            if key in seen:
                detail = "duplicate organization membership"
            elif key[0] not in org_ids or key[1] not in user_ids:
                detail = "membership references a user or organization absent from the workbook"
            if detail:
                actions.append({
                    "scope": "workbook", "object_type": "organization_member",
                    "object_id": "|".join(key), "action": "conflict", "detail": detail,
                })
            seen.add(key)
        return _report(self.resource_type, workbook.resource_id, actions)

    async def apply(self, context: ImportExportContext, workbook: WorkbookData) -> dict[str, Any]:
        users, orgs, memberships = self._decode(workbook)
        report = await self.preflight(context, workbook)
        if not report["can_import"]:
            raise ValueError("workbook has blocking preflight issues")
        created: list[dict[str, str]] = []
        new_org_ids: set[str] = set()
        for org in orgs:
            group_id = str(org.get("group_id") or "")
            existing = await _identity_request(
                "GET", f"/v1/orgs/{quote(group_id, safe='')}", context.authorization
            )
            if existing is not None:
                continue
            await _identity_request(
                "POST", "/v1/orgs/", context.authorization,
                body={"group_id": group_id, "display_name": str(org.get("display_name") or group_id)},
            )
            status = str(org.get("status") or "active")
            if status != "active":
                await _identity_request(
                    "PATCH", f"/v1/orgs/{quote(group_id, safe='')}", context.authorization,
                    body={"status": status},
                )
            new_org_ids.add(group_id)
            created.append({"object_type": "organization", "object_id": group_id})
        required_user_ids = {
            str(row.get("user_id") or "")
            for row in memberships
            if str(row.get("group_id") or "") in new_org_ids
        }
        created.extend(await _apply_users(
            context,
            [row for row in users if str(row.get("user_id") or "") in required_user_ids],
        ))
        members_by_org: dict[str, list[str]] = {}
        for row in memberships:
            group_id = str(row.get("group_id") or "")
            if group_id in new_org_ids:
                members_by_org.setdefault(group_id, []).append(str(row.get("user_id") or ""))
        for group_id, user_ids in members_by_org.items():
            await _identity_request(
                "POST", f"/v1/orgs/{quote(group_id, safe='')}/members", context.authorization,
                body={"user_ids": user_ids},
            )
        return {
            "resource_type": self.resource_type, "resource_id": workbook.resource_id,
            "created_manager_objects": 0, "created_identity_objects": created,
            "reused_objects": report["summary"].get("reuse", 0), "pending_sync": [],
        }


class RoleAdapter:
    resource_type = "role"

    async def export(self, context: ImportExportContext, resource_id: str) -> WorkbookData:
        return await self.export_many(context, [resource_id])

    async def export_many(
        self, context: ImportExportContext, resource_ids: list[str]
    ) -> WorkbookData:
        roles: list[dict[str, Any]] = []
        permissions: list[dict[str, Any]] = []
        assignments: list[dict[str, Any]] = []
        user_ids: set[str] = set()
        for role_id in resource_ids:
            role = await context.handler.get(AUTHZ_ROLE_TABLE_DEF.table_name, {"role_id": role_id})
            if role is None:
                raise LookupError(f"role not found: {role_id}")
            roles.append({
                "role_id": role_id, "name": getattr(role, "name", ""),
                "description": getattr(role, "description", None), "scope": getattr(role, "scope", "admin"),
                "is_system": bool(getattr(role, "is_system", False)),
                "enabled": bool(getattr(role, "enabled", True)),
            })
            for binding in await _list(context.handler, AUTHZ_ROLE_PERMISSION_TABLE_DEF.table_name, {"role_id": role_id}):
                permissions.append({"role_id": role_id, "permission_id": getattr(binding, "permission_id", "")})
            for binding in await _list(context.handler, AUTHZ_ROLE_USER_TABLE_DEF.table_name, {"role_id": role_id}):
                user_id = str(getattr(binding, "user_id", "") or "")
                user_ids.add(user_id)
                assignments.append({
                    "role_id": role_id, "user_id": user_id,
                    "expires_at": getattr(binding, "expires_at", None),
                })
        users = await _fetch_users(context.authorization, sorted(user_ids))
        return WorkbookData(
            resource_type=self.resource_type,
            resource_id=_selection_id(resource_ids),
            resource_name="Role" if len(resource_ids) == 1 else "RoleSelection",
            sheets=[
                SheetData("02_Users", _USER_HEADERS, users,
                          "Referenced assignee profiles; fill initial_password only for missing local users.",
                          {(index, "initial_password") for index in range(len(users))}),
                SheetData("40_Roles", ["role_id", "name", "description", "scope", "is_system", "enabled"], roles),
                SheetData("41_RolePermissions", ["role_id", "permission_id"], permissions),
                SheetData("42_RoleAssignments", ["role_id", "user_id", "expires_at"], assignments),
            ],
        )

    def _decode(self, workbook: WorkbookData):
        sheets = _sheet_map(workbook)
        for required in ("02_Users", "40_Roles", "41_RolePermissions", "42_RoleAssignments"):
            if required not in sheets:
                raise ValueError(f"missing required sheet: {required}")
        return (
            sheets["02_Users"].rows,
            sheets["40_Roles"].rows,
            sheets["41_RolePermissions"].rows,
            sheets["42_RoleAssignments"].rows,
        )

    async def preflight(
        self, context: ImportExportContext, workbook: WorkbookData
    ) -> dict[str, Any]:
        users, roles, permissions, assignments = self._decode(workbook)
        actions = _identity_duplicates("user", "user_id", users)
        actions.extend(_identity_duplicates("role", "role_id", roles))
        missing_role_ids: set[str] = set()
        for role in roles:
            role_id = str(role.get("role_id") or "")
            existing = await context.handler.get(
                AUTHZ_ROLE_TABLE_DEF.table_name, {"role_id": role_id}
            )
            if existing is None:
                missing_role_ids.add(role_id)
        required_user_ids = {
            str(item.get("user_id") or "")
            for item in assignments
            if str(item.get("role_id") or "") in missing_role_ids
        }
        actions.extend(await _identity_actions(
            context.authorization,
            [row for row in users if str(row.get("user_id") or "") in required_user_ids],
            [],
        ))
        permission_map: dict[str, set[str]] = {}
        for item in permissions:
            role_id = str(item.get("role_id") or "")
            permission_id = str(item.get("permission_id") or "")
            permission_map.setdefault(role_id, set()).add(permission_id)
            existing = await context.handler.get(
                AUTHZ_PERMISSION_TABLE_DEF.table_name, {"permission_id": permission_id}
            )
            if existing is None:
                actions.append({
                    "scope": "manager", "object_type": "authz_permission",
                    "object_id": permission_id, "action": "missing_dependency",
                    "detail": f"permission required by role {role_id} does not exist",
                })
        role_ids = {str(item.get("role_id") or "") for item in roles}
        user_ids = {str(item.get("user_id") or "") for item in users}
        for item in assignments:
            role_id = str(item.get("role_id") or "")
            user_id = str(item.get("user_id") or "")
            if role_id not in role_ids or user_id not in user_ids:
                actions.append({
                    "scope": "workbook", "object_type": "authz_role_user",
                    "object_id": f"{role_id}|{user_id}", "action": "missing_dependency",
                    "detail": "assignment references a role or user absent from the workbook",
                })
        for role in roles:
            role_id = str(role.get("role_id") or "")
            existing = await context.handler.get(AUTHZ_ROLE_TABLE_DEF.table_name, {"role_id": role_id})
            if existing is None:
                if bool(role.get("is_system")):
                    action, detail = "conflict", "system roles may only be reused"
                else:
                    action, detail = "create", ""
            else:
                desired = {
                    key: role.get(key) for key in ("name", "description", "scope", "is_system", "enabled")
                }
                existing_value = {key: getattr(existing, key, None) for key in desired}
                current_permissions = {
                    str(getattr(item, "permission_id", ""))
                    for item in await _list(
                        context.handler, AUTHZ_ROLE_PERMISSION_TABLE_DEF.table_name, {"role_id": role_id}
                    )
                }
                if _matches(existing_value, desired) and current_permissions == permission_map.get(role_id, set()):
                    action, detail = "reuse", "existing role is reused; assignments are not overwritten"
                else:
                    action, detail = "conflict", "same role_id exists with different role or permission configuration"
            actions.append({
                "scope": "manager", "object_type": "authz_role", "object_id": role_id,
                "action": action, "detail": detail,
            })
            if (
                action == "create"
                and str(role.get("scope") or "") == "admin"
                and any(
                    str(item.get("role_id") or "") == role_id
                    for item in assignments
                )
            ):
                actions.append({
                    "scope": "manager", "object_type": "authz_role_user", "object_id": role_id,
                    "action": "warning", "detail": "import creates admin-scope role assignments",
                })
        return _report(self.resource_type, workbook.resource_id, actions)

    async def apply(self, context: ImportExportContext, workbook: WorkbookData) -> dict[str, Any]:
        users, roles, permissions, assignments = self._decode(workbook)
        report = await self.preflight(context, workbook)
        if not report["can_import"]:
            raise ValueError("workbook has blocking preflight issues")
        target_role_ids = {
            str(role.get("role_id") or "")
            for role in roles
            if await context.handler.get(
                AUTHZ_ROLE_TABLE_DEF.table_name,
                {"role_id": str(role.get("role_id") or "")},
            ) is None
        }
        required_user_ids = {
            str(item.get("user_id") or "")
            for item in assignments
            if str(item.get("role_id") or "") in target_role_ids
        }
        created_identity = await _apply_users(
            context,
            [row for row in users if str(row.get("user_id") or "") in required_user_ids],
        )
        permission_map: dict[str, list[str]] = {}
        for item in permissions:
            permission_map.setdefault(str(item.get("role_id") or ""), []).append(
                str(item.get("permission_id") or "")
            )
        new_roles: set[str] = set()
        service = AuthzService(context.handler)
        for role in roles:
            role_id = str(role.get("role_id") or "")
            if await context.handler.get(AUTHZ_ROLE_TABLE_DEF.table_name, {"role_id": role_id}) is not None:
                continue
            await service.create_role(
                role_id=role_id,
                name=str(role.get("name") or role_id),
                description=role.get("description"),
                scope=str(role.get("scope") or "admin"),
                permission_ids=permission_map.get(role_id, []),
                operator_id=context.operator_id,
            )
            if not bool(role.get("enabled", True)):
                await service.update_role(
                    role_id, name=None, description=None, scope=None, enabled=False,
                    permission_ids=None, operator_id=context.operator_id,
                )
            new_roles.add(role_id)
        now = datetime.now(UTC)
        created_assignments = 0
        for item in assignments:
            role_id = str(item.get("role_id") or "")
            if role_id not in new_roles:
                continue
            user_id = str(item.get("user_id") or "")
            expires_at = item.get("expires_at")
            if isinstance(expires_at, str) and expires_at:
                expires_at = datetime.fromisoformat(expires_at)
            await context.handler.create(AUTHZ_ROLE_USER_TABLE_DEF.table_name, {
                "role_id": role_id, "user_id": user_id,
                "granted_by": context.operator_id, "expires_at": expires_at or None,
                "data": None, "created_at": now, "created_by": context.operator_id,
                "updated_at": now, "updated_by": context.operator_id,
            })
            created_assignments += 1
        return {
            "resource_type": self.resource_type, "resource_id": workbook.resource_id,
            "created_manager_objects": len(new_roles) + created_assignments,
            "created_identity_objects": created_identity,
            "reused_objects": report["summary"].get("reuse", 0), "pending_sync": [],
        }


for _kind in _SIMPLE_KINDS:
    adapter_registry.register(CatalogAdapter(_kind))
adapter_registry.register(A2APolicyAdapter())
adapter_registry.register(AgentTemplateAdapter())
adapter_registry.register(UserAdapter())
adapter_registry.register(OrganizationAdapter())
adapter_registry.register(RoleAdapter())
