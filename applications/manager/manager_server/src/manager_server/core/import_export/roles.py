"""Shared role dependency handling for standalone and cluster workbooks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.authz import AuthzService
from manager_server.infrastructure.utils import utc_now
from manager_server.models.authz_models import (
    AUTHZ_PERMISSION_TABLE_DEF,
    AUTHZ_ROLE_PERMISSION_TABLE_DEF,
    AUTHZ_ROLE_TABLE_DEF,
    AUTHZ_ROLE_USER_TABLE_DEF,
)
from manager_server.schemas.authz_schemas import RoleCreateBody

from .registry import SheetData, WorkbookData

ROLE_SHEET_NAMES = ("40_Roles", "41_RolePermissions", "42_RoleAssignments")
ROLE_HEADERS = ["role_id", "name", "description", "scope", "is_system", "enabled"]
ROLE_PERMISSION_HEADERS = ["role_id", "permission_id"]
ROLE_ASSIGNMENT_HEADERS = ["role_id", "user_id", "expires_at"]
_CAP = 100_000


@dataclass(slots=True)
class RoleRows:
    roles: list[dict[str, Any]]
    permissions: list[dict[str, Any]]
    assignments: list[dict[str, Any]]
    present: bool = True

    def sheets(self) -> list[SheetData]:
        return [
            SheetData(
                "40_Roles",
                ROLE_HEADERS,
                self.roles,
                "Role definitions referenced by the exported user-role assignments.",
            ),
            SheetData(
                "41_RolePermissions",
                ROLE_PERMISSION_HEADERS,
                self.permissions,
                "Permission bindings for exported roles; permission definitions are target-owned.",
            ),
            SheetData(
                "42_RoleAssignments",
                ROLE_ASSIGNMENT_HEADERS,
                self.assignments,
                "Exported user-role assignments, including assignment expiration.",
            ),
        ]


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no", ""}:
            return False
    return bool(value)


def _normalize_expiry(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        value = datetime.fromisoformat(text)
    if not isinstance(value, datetime):
        raise TypeError("expires_at must be an ISO-8601 datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _same_expiry(left: Any, right: Any) -> bool:
    return _normalize_expiry(left) == _normalize_expiry(right)


def _is_effective(expiry: Any) -> bool:
    normalized = _normalize_expiry(expiry)
    return normalized is None or normalized > utc_now()


async def _list(handler: DBHandler, table: str, filters: dict[str, Any]) -> list[Any]:
    return list(await handler.list_records(table, filters, limit=_CAP, offset=0))


def _role_dict(row: Any) -> dict[str, Any]:
    return {
        "role_id": str(_get(row, "role_id") or ""),
        "name": _get(row, "name", ""),
        "description": _get(row, "description"),
        "scope": _get(row, "scope", "admin"),
        "is_system": _as_bool(_get(row, "is_system", False)),
        "enabled": _as_bool(_get(row, "enabled", True)),
    }


async def collect_roles_by_ids(
    handler: DBHandler,
    role_ids: list[str] | set[str],
    *,
    assignment_user_ids: set[str] | None = None,
) -> RoleRows:
    roles: list[dict[str, Any]] = []
    permissions: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    for role_id in sorted({str(item).strip() for item in role_ids if str(item).strip()}):
        role = await handler.get(AUTHZ_ROLE_TABLE_DEF.table_name, {"role_id": role_id})
        if role is None:
            raise LookupError(f"role not found: {role_id}")
        roles.append(_role_dict(role))
        for binding in await _list(
            handler,
            AUTHZ_ROLE_PERMISSION_TABLE_DEF.table_name,
            {"role_id": role_id},
        ):
            permissions.append(
                {
                    "role_id": role_id,
                    "permission_id": str(_get(binding, "permission_id") or ""),
                }
            )
        for binding in await _list(
            handler,
            AUTHZ_ROLE_USER_TABLE_DEF.table_name,
            {"role_id": role_id},
        ):
            user_id = str(_get(binding, "user_id") or "")
            if assignment_user_ids is not None and user_id not in assignment_user_ids:
                continue
            assignments.append(
                {
                    "role_id": role_id,
                    "user_id": user_id,
                    "expires_at": _get(binding, "expires_at"),
                }
            )
    permissions.sort(key=lambda item: (item["role_id"], item["permission_id"]))
    assignments.sort(key=lambda item: (item["role_id"], item["user_id"]))
    return RoleRows(roles, permissions, assignments)


async def collect_roles_for_users(handler: DBHandler, user_ids: set[str]) -> RoleRows:
    normalized_user_ids = {str(item).strip() for item in user_ids if str(item).strip()}
    role_ids: set[str] = set()
    for user_id in sorted(normalized_user_ids):
        for binding in await _list(
            handler,
            AUTHZ_ROLE_USER_TABLE_DEF.table_name,
            {"user_id": user_id},
        ):
            role_id = str(_get(binding, "role_id") or "")
            if role_id:
                role_ids.add(role_id)
    return await collect_roles_by_ids(
        handler,
        role_ids,
        assignment_user_ids=normalized_user_ids,
    )


def decode_role_sheets(workbook: WorkbookData, *, required: bool) -> RoleRows:
    sheets = {sheet.name: sheet for sheet in workbook.sheets}
    present = {name for name in ROLE_SHEET_NAMES if name in sheets}
    if not present:
        if required:
            raise ValueError(f"missing required role sheets: {' / '.join(ROLE_SHEET_NAMES)}")
        return RoleRows([], [], [], present=False)
    if present != set(ROLE_SHEET_NAMES):
        missing = [name for name in ROLE_SHEET_NAMES if name not in present]
        raise ValueError(f"incomplete role sheet set; missing: {', '.join(missing)}")
    return RoleRows(
        sheets["40_Roles"].rows,
        sheets["41_RolePermissions"].rows,
        sheets["42_RoleAssignments"].rows,
    )


def _duplicate_actions(
    rows: list[dict[str, Any]],
    *,
    object_type: str,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for index, row in enumerate(rows, start=2):
        signature = tuple(str(row.get(key) or "").strip() for key in keys)
        missing = [key for key, value in zip(keys, signature, strict=True) if not value]
        object_id = "|".join(signature) or f"row:{index}"
        if missing:
            actions.append(
                {
                    "scope": "workbook",
                    "object_type": object_type,
                    "object_id": object_id,
                    "action": "missing_dependency",
                    "detail": f"missing business key column(s): {', '.join(missing)}",
                }
            )
            continue
        if signature in seen:
            actions.append(
                {
                    "scope": "workbook",
                    "object_type": object_type,
                    "object_id": object_id,
                    "action": "conflict",
                    "detail": f"duplicate relationship at decoded row {index}",
                }
            )
        seen.add(signature)
    return actions


async def role_preflight_actions(
    handler: DBHandler,
    *,
    users: list[dict[str, Any]],
    role_rows: RoleRows,
) -> list[dict[str, Any]]:
    if not role_rows.present:
        return []
    roles = role_rows.roles
    permissions = role_rows.permissions
    assignments = role_rows.assignments
    actions = _duplicate_actions(roles, object_type="authz_role", keys=("role_id",))
    actions.extend(
        _duplicate_actions(
            permissions,
            object_type="authz_role_permission",
            keys=("role_id", "permission_id"),
        )
    )
    actions.extend(
        _duplicate_actions(
            assignments,
            object_type="authz_role_user",
            keys=("role_id", "user_id"),
        )
    )

    role_by_id = {
        str(row.get("role_id") or "").strip(): row
        for row in roles
        if str(row.get("role_id") or "").strip()
    }
    user_ids = {
        str(row.get("user_id") or "").strip()
        for row in users
        if str(row.get("user_id") or "").strip()
    }
    permission_map: dict[str, set[str]] = {role_id: set() for role_id in role_by_id}
    for item in permissions:
        role_id = str(item.get("role_id") or "").strip()
        permission_id = str(item.get("permission_id") or "").strip()
        if not role_id or not permission_id:
            continue
        role = role_by_id.get(role_id)
        if role is None:
            actions.append(
                {
                    "scope": "workbook",
                    "object_type": "authz_role_permission",
                    "object_id": f"{role_id}|{permission_id}",
                    "action": "missing_dependency",
                    "detail": "permission binding references a role absent from 40_Roles",
                }
            )
            continue
        permission_map[role_id].add(permission_id)
        target_permission = await handler.get(
            AUTHZ_PERMISSION_TABLE_DEF.table_name,
            {"permission_id": permission_id},
        )
        if target_permission is None or not _as_bool(_get(target_permission, "enabled", True)):
            actions.append(
                {
                    "scope": "manager",
                    "object_type": "authz_permission",
                    "object_id": permission_id,
                    "action": "missing_dependency",
                    "detail": f"permission required by role {role_id} is missing or disabled",
                }
            )
        elif str(_get(target_permission, "scope") or "") != str(role.get("scope") or ""):
            actions.append(
                {
                    "scope": "manager",
                    "object_type": "authz_permission",
                    "object_id": permission_id,
                    "action": "conflict",
                    "detail": f"permission scope does not match role {role_id}",
                }
            )

    role_actions: dict[str, str] = {}
    for role_id, role in role_by_id.items():
        try:
            RoleCreateBody.model_validate(
                {
                    "role_id": role_id,
                    "name": role.get("name"),
                    "description": role.get("description"),
                    "scope": role.get("scope"),
                    "permission_ids": sorted(permission_map[role_id]),
                }
            )
        except (TypeError, ValueError) as exc:
            actions.append(
                {
                    "scope": "workbook",
                    "object_type": "authz_role",
                    "object_id": role_id,
                    "action": "conflict",
                    "detail": f"invalid role configuration: {exc}",
                }
            )
        existing = await handler.get(AUTHZ_ROLE_TABLE_DEF.table_name, {"role_id": role_id})
        if existing is None:
            if _as_bool(role.get("is_system")):
                action, detail = "conflict", "system roles may only be reused"
            else:
                action, detail = "create", ""
        else:
            desired = {
                "name": role.get("name"),
                "description": role.get("description"),
                "scope": role.get("scope"),
                "is_system": _as_bool(role.get("is_system")),
                "enabled": _as_bool(role.get("enabled", True)),
            }
            existing_value = {
                "name": _get(existing, "name"),
                "description": _get(existing, "description"),
                "scope": _get(existing, "scope"),
                "is_system": _as_bool(_get(existing, "is_system", False)),
                "enabled": _as_bool(_get(existing, "enabled", True)),
            }
            current_permissions = {
                str(_get(item, "permission_id") or "")
                for item in await _list(
                    handler,
                    AUTHZ_ROLE_PERMISSION_TABLE_DEF.table_name,
                    {"role_id": role_id},
                )
            }
            if existing_value == desired and current_permissions == permission_map[role_id]:
                action, detail = "reuse", "existing role is reused without modification"
            else:
                action = "conflict"
                detail = "same role_id exists with different role or permission configuration"
        role_actions[role_id] = action
        actions.append(
            {
                "scope": "manager",
                "object_type": "authz_role",
                "object_id": role_id,
                "action": action,
                "detail": detail,
            }
        )

    for item in assignments:
        role_id = str(item.get("role_id") or "").strip()
        user_id = str(item.get("user_id") or "").strip()
        if not role_id or not user_id:
            continue
        if role_id not in role_by_id or user_id not in user_ids:
            actions.append(
                {
                    "scope": "workbook",
                    "object_type": "authz_role_user",
                    "object_id": f"{role_id}|{user_id}",
                    "action": "missing_dependency",
                    "detail": "assignment references a role or user absent from the workbook",
                }
            )
            continue
        try:
            desired_expiry = _normalize_expiry(item.get("expires_at"))
        except (TypeError, ValueError) as exc:
            actions.append(
                {
                    "scope": "workbook",
                    "object_type": "authz_role_user",
                    "object_id": f"{role_id}|{user_id}",
                    "action": "conflict",
                    "detail": str(exc),
                }
            )
            continue
        existing = await handler.get(
            AUTHZ_ROLE_USER_TABLE_DEF.table_name,
            {"role_id": role_id, "user_id": user_id},
        )
        if existing is None:
            action, detail = "create", "missing role assignment will be added"
        elif _same_expiry(_get(existing, "expires_at"), desired_expiry):
            action, detail = "reuse", "existing role assignment is reused"
        else:
            action = "conflict"
            detail = "same role and user assignment exists with a different expiration"
        actions.append(
            {
                "scope": "manager",
                "object_type": "authz_role_user",
                "object_id": f"{role_id}|{user_id}",
                "action": action,
                "detail": detail,
            }
        )
        role = role_by_id[role_id]
        if (
            action == "create"
            and role_actions.get(role_id) in {"create", "reuse"}
            and str(role.get("scope") or "") == "admin"
            and _is_effective(desired_expiry)
        ):
            actions.append(
                {
                    "scope": "manager",
                    "object_type": "authz_role_user",
                    "object_id": f"{role_id}|{user_id}",
                    "action": "warning",
                    "detail": "import creates an effective admin-scope role assignment",
                }
            )
    return actions


async def apply_role_rows(
    handler: DBHandler,
    *,
    role_rows: RoleRows,
    operator_id: str,
) -> tuple[int, int]:
    if not role_rows.present:
        return 0, 0
    permission_map: dict[str, list[str]] = {}
    for item in role_rows.permissions:
        role_id = str(item.get("role_id") or "").strip()
        permission_id = str(item.get("permission_id") or "").strip()
        if role_id and permission_id:
            permission_map.setdefault(role_id, []).append(permission_id)

    created_roles = 0
    service = AuthzService(handler)
    for role in role_rows.roles:
        role_id = str(role.get("role_id") or "").strip()
        if await handler.get(AUTHZ_ROLE_TABLE_DEF.table_name, {"role_id": role_id}) is not None:
            continue
        await service.create_role(
            role_id=role_id,
            name=str(role.get("name") or role_id),
            description=role.get("description"),
            scope=str(role.get("scope") or "admin"),
            permission_ids=permission_map.get(role_id, []),
            operator_id=operator_id,
        )
        if not _as_bool(role.get("enabled", True)):
            await service.update_role(
                role_id,
                name=None,
                description=None,
                scope=None,
                enabled=False,
                permission_ids=None,
                operator_id=operator_id,
            )
        created_roles += 1

    created_assignments = 0
    now = utc_now()
    for item in role_rows.assignments:
        role_id = str(item.get("role_id") or "").strip()
        user_id = str(item.get("user_id") or "").strip()
        filters = {"role_id": role_id, "user_id": user_id}
        if await handler.get(AUTHZ_ROLE_USER_TABLE_DEF.table_name, filters) is not None:
            continue
        await handler.create(
            AUTHZ_ROLE_USER_TABLE_DEF.table_name,
            {
                **filters,
                "granted_by": operator_id,
                "expires_at": _normalize_expiry(item.get("expires_at")),
                "data": None,
                "created_at": now,
                "created_by": operator_id,
                "updated_at": now,
                "updated_by": operator_id,
            },
        )
        created_assignments += 1
    return created_roles, created_assignments
