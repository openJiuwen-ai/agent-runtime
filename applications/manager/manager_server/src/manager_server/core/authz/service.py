"""角色、权限、指派与权限解析服务。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.log import get_logger

from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, strip_optional, utc_now
from manager_server.models.authz_models import (
    AUTHZ_PERMISSION_TABLE_DEF,
    AUTHZ_ROLE_PERMISSION_TABLE_DEF,
    AUTHZ_ROLE_TABLE_DEF,
    AUTHZ_ROLE_USER_TABLE_DEF,
)
from manager_server.schemas.authz_schemas import PermissionListQuery, RoleListQuery, RoleUserListQuery

_PERMISSION_TABLE = AUTHZ_PERMISSION_TABLE_DEF.table_name
_ROLE_TABLE = AUTHZ_ROLE_TABLE_DEF.table_name
_ROLE_PERMISSION_TABLE = AUTHZ_ROLE_PERMISSION_TABLE_DEF.table_name
_ROLE_USER_TABLE = AUTHZ_ROLE_USER_TABLE_DEF.table_name
_CAP = 100_000
_ALLOWED_SORT_FIELDS = frozenset({"name", "description", "updated_at", "role_id"})
_ALLOWED_ROLE_USER_SORT_FIELDS = frozenset({
    "user_id",
    "granted_by",
    "expires_at",
    "created_at",
    "updated_at",
})
_DEFAULT_ROLE_ORDER_BY: list[tuple[str, bool]] = [("role_id", False)]
_DEFAULT_ROLE_USER_ORDER_BY: list[tuple[str, bool]] = [("user_id", False)]
_log = get_logger(__name__)

PLATFORM_ADMIN_ROLE_ID = "platform_admin"
VALID_SCOPES = frozenset({"admin", "org", "user"})
_ROLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

PERMISSION_SEEDS: tuple[dict[str, str], ...] = (
    {
        "permission_id": "quota:read",
        "name": "查看配额",
        "description": "查看配额策略、生效结果与用量排行",
        "resource_type": "quota",
        "action": "read",
        "scope": "admin",
    },
    {
        "permission_id": "quota:write",
        "name": "管理配额",
        "description": "新增、修改、停用或删除配额策略",
        "resource_type": "quota",
        "action": "write",
        "scope": "admin",
    },
    {
        "permission_id": "approval:read",
        "name": "查看审批",
        "description": "查看扩容审批单",
        "resource_type": "approval",
        "action": "read",
        "scope": "admin",
    },
    {
        "permission_id": "approval:act",
        "name": "处理审批",
        "description": "通过或驳回扩容审批单",
        "resource_type": "approval",
        "action": "act",
        "scope": "admin",
    },
    {
        "permission_id": "iam:role:read",
        "name": "查看角色",
        "description": "查看角色、权限和角色指派",
        "resource_type": "iam",
        "action": "read",
        "scope": "admin",
    },
    {
        "permission_id": "iam:role:write",
        "name": "管理角色",
        "description": "新建、修改、停用自定义角色，绑定权限码，以及把角色指派给用户",
        "resource_type": "iam",
        "action": "write",
        "scope": "admin",
    },
)


def _g(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _active_assignment(row: Any) -> bool:
    expires_at = _g(row, "expires_at")
    if expires_at is None:
        return True
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            return False
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > utc_now()


def _permission_out(row: Any) -> dict[str, Any]:
    return {
        "permission_id": _g(row, "permission_id"),
        "name": _g(row, "name"),
        "description": _g(row, "description"),
        "resource_type": _g(row, "resource_type"),
        "action": _g(row, "action"),
        "scope": _g(row, "scope"),
        "enabled": bool(_g(row, "enabled", True)),
    }


def _matches_role_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(_g(row, "role_id", "") or ""),
        str(_g(row, "name", "") or ""),
        str(_g(row, "description", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def _matches_permission_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(_g(row, "permission_id", "") or ""),
        str(_g(row, "name", "") or ""),
        str(_g(row, "description", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def _matches_role_user_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(_g(row, "user_id", "") or ""),
        str(_g(row, "granted_by", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def _role_user_out(row: Any) -> dict[str, Any]:
    return {
        "role_id": _g(row, "role_id"),
        "user_id": _g(row, "user_id"),
        "granted_by": _g(row, "granted_by"),
        "expires_at": iso_datetime(_g(row, "expires_at")),
        "created_at": iso_datetime(_g(row, "created_at")),
        "created_by": _g(row, "created_by"),
        "updated_at": iso_datetime(_g(row, "updated_at")),
        "updated_by": _g(row, "updated_by"),
    }


def _parse_sort_spec(
    sort_by: str | None,
    sort_order: str | None,
    allowed_fields: frozenset[str],
) -> tuple[str, str] | None:
    """解析排序字段与方向；非法时返回 None。"""
    field = (sort_by or "").strip()
    order = (sort_order or "").strip().lower()
    if not field or not order:
        return None
    if field not in allowed_fields or order not in {"asc", "desc"}:
        return None
    return field, order


def _sort_role_user_items(
    items: list[dict[str, Any]],
    sort_by: str | None,
    sort_order: str | None,
) -> None:
    """就地排序；未指定排序时按 user_id 升序。"""
    parsed = _parse_sort_spec(sort_by, sort_order, _ALLOWED_ROLE_USER_SORT_FIELDS)
    if parsed is None:
        items.sort(key=lambda item: str(item.get("user_id") or ""))
        return

    field, order = parsed
    order_by = resolve_order_by(
        field,
        order,
        allowed_sort_fields=_ALLOWED_ROLE_USER_SORT_FIELDS,
        default_order_by=_DEFAULT_ROLE_USER_ORDER_BY,
    )
    primary_field, is_desc = order_by[0]

    def sort_key(item: dict[str, Any]) -> tuple[Any, str]:
        value = item.get(primary_field)
        if value is None:
            value = ""
        return (value, str(item.get("user_id") or ""))

    items.sort(key=sort_key, reverse=is_desc)


def _sort_role_items(
    items: list[dict[str, Any]],
    sort_by: str | None,
    sort_order: str | None,
) -> None:
    """就地排序；未指定排序时系统角色优先，再按 role_id 升序。"""
    parsed = _parse_sort_spec(sort_by, sort_order, _ALLOWED_SORT_FIELDS)
    if parsed is None:
        items.sort(
            key=lambda item: (not bool(item.get("is_system")), str(item.get("role_id") or "")),
        )
        return

    field, order = parsed
    order_by = resolve_order_by(
        field,
        order,
        allowed_sort_fields=_ALLOWED_SORT_FIELDS,
        default_order_by=_DEFAULT_ROLE_ORDER_BY,
    )
    primary_field, is_desc = order_by[0]

    def sort_key(item: dict[str, Any]) -> tuple[Any, str]:
        value = item.get(primary_field)
        if value is None:
            value = ""
        return (value, str(item.get("role_id") or ""))

    items.sort(key=sort_key, reverse=is_desc)


async def seed_authz_defaults(handler: DBHandler) -> None:
    """幂等播种本需求的权限码、平台管理员角色与绑定。"""
    now = utc_now()
    for seed in PERMISSION_SEEDS:
        permission_id = seed["permission_id"]
        existing = await handler.get(_PERMISSION_TABLE, {"permission_id": permission_id})
        values = {
            **seed,
            "enabled": True,
            "data": None,
            "updated_at": now,
            "updated_by": "system",
        }
        if existing is None:
            await handler.create(
                _PERMISSION_TABLE,
                {**values, "created_at": now, "created_by": "system"},
            )
        else:
            await handler.update(_PERMISSION_TABLE, {"permission_id": permission_id}, values)

    role = await handler.get(_ROLE_TABLE, {"role_id": PLATFORM_ADMIN_ROLE_ID})
    role_values = {
        "name": "平台管理员",
        "description": "系统预置角色，拥有工作区配额、审批与角色管理权限",
        "scope": "admin",
        "is_system": True,
        "enabled": True,
        "data": None,
        "updated_at": now,
        "updated_by": "system",
    }
    if role is None:
        await handler.create(
            _ROLE_TABLE,
            {
                "role_id": PLATFORM_ADMIN_ROLE_ID,
                **role_values,
                "created_at": now,
                "created_by": "system",
            },
        )

    for seed in PERMISSION_SEEDS:
        filters = {
            "role_id": PLATFORM_ADMIN_ROLE_ID,
            "permission_id": seed["permission_id"],
        }
        if await handler.get(_ROLE_PERMISSION_TABLE, filters) is None:
            await handler.create(
                _ROLE_PERMISSION_TABLE,
                {
                    **filters,
                    "data": None,
                    "created_at": now,
                    "created_by": "system",
                    "updated_at": now,
                    "updated_by": "system",
                },
            )

    # 预置本地 admin 用户绑定 platform_admin（不再依赖 identity_user.is_admin 短路）
    admin_assignment = {
        "role_id": PLATFORM_ADMIN_ROLE_ID,
        "user_id": "admin",
    }
    if await handler.get(_ROLE_USER_TABLE, admin_assignment) is None:
        await handler.create(
            _ROLE_USER_TABLE,
            {
                **admin_assignment,
                "granted_by": "system",
                "expires_at": None,
                "data": {"source": "seed_authz_defaults"},
                "created_at": now,
                "created_by": "system",
                "updated_at": now,
                "updated_by": "system",
            },
        )

    # 合并历史权限码：iam:role:assign → iam:role:write；quota:usage:read → quota:read
    for deprecated_id in ("iam:role:assign", "quota:usage:read"):
        for binding in await handler.list_records(
            _ROLE_PERMISSION_TABLE,
            filters={"permission_id": deprecated_id},
            limit=_CAP,
        ):
            await handler.delete(
                _ROLE_PERMISSION_TABLE,
                {
                    "role_id": _g(binding, "role_id"),
                    "permission_id": deprecated_id,
                },
            )
        if await handler.get(_PERMISSION_TABLE, {"permission_id": deprecated_id}) is not None:
            await handler.delete(_PERMISSION_TABLE, {"permission_id": deprecated_id})


class AuthzService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list_permissions(
        self,
        query: PermissionListQuery | None = None,
    ) -> list[dict[str, Any]]:
        """筛选与搜索均在服务端完成。"""
        query = query or PermissionListQuery()
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        if query.scope is not None:
            filters["scope"] = query.scope

        rows = await self._h.list_records(_PERMISSION_TABLE, filters, limit=_CAP, offset=0)
        search_query = (query.search or "").strip()
        if search_query:
            rows = [row for row in rows if _matches_permission_search(row, search_query)]

        return sorted(
            (_permission_out(row) for row in rows),
            key=lambda item: str(item["permission_id"]),
        )

    async def permissions_for_user(self, user_id: str) -> set[str]:
        assignments = await self._h.list_records(
            _ROLE_USER_TABLE, {"user_id": user_id}, limit=_CAP, offset=0
        )
        role_ids: list[str] = []
        for assignment in assignments:
            if not _active_assignment(assignment):
                continue
            role_id = str(_g(assignment, "role_id") or "")
            role = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
            if role is not None and bool(_g(role, "enabled", True)):
                role_ids.append(role_id)

        result: set[str] = set()
        for role_id in role_ids:
            bindings = await self._h.list_records(
                _ROLE_PERMISSION_TABLE, {"role_id": role_id}, limit=_CAP, offset=0
            )
            for binding in bindings:
                permission_id = str(_g(binding, "permission_id") or "")
                permission = await self._h.get(_PERMISSION_TABLE, {"permission_id": permission_id})
                if permission is not None and bool(_g(permission, "enabled", True)):
                    result.add(permission_id)
        return result

    async def role_ids_for_user(self, user_id: str) -> list[str]:
        rows = await self._h.list_records(
            _ROLE_USER_TABLE, {"user_id": user_id}, limit=_CAP, offset=0
        )
        result: list[str] = []
        for row in rows:
            if not _active_assignment(row):
                continue
            role_id = str(_g(row, "role_id") or "")
            role = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
            if role is not None and bool(_g(role, "enabled", True)):
                result.append(role_id)
        return sorted(set(result))

    async def has_permission(self, user: Any, permission_id: str) -> bool:
        """仅依据本地角色指派判定；不再读取 identity_user.is_admin。"""
        user_id = str(getattr(user, "user_id", "") or "")
        if not user_id:
            return False
        return permission_id in await self.permissions_for_user(user_id)

    async def list_users_with_permission(self, permission_id: str) -> list[str]:
        """返回持有指定权限码的启用用户（去重排序）。"""
        permission_id = str(permission_id or "").strip()
        if not permission_id:
            return []
        permission = await self._h.get(_PERMISSION_TABLE, {"permission_id": permission_id})
        if permission is None or not bool(_g(permission, "enabled", True)):
            return []
        bindings = await self._h.list_records(
            _ROLE_PERMISSION_TABLE, {"permission_id": permission_id}, limit=_CAP, offset=0
        )
        user_ids: set[str] = set()
        for binding in bindings:
            role_id = str(_g(binding, "role_id") or "").strip()
            if not role_id:
                continue
            role = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
            if role is None or not bool(_g(role, "enabled", True)):
                continue
            for user_id in await self._user_ids_for_role(role_id):
                if user_id:
                    user_ids.add(user_id)
        return sorted(user_ids)

    async def has_admin_scope_role(self, user_id: str) -> bool:
        """是否持有启用中的 admin 类型角色（自定义管理员角色也算）。"""
        for role_id in await self.role_ids_for_user(user_id):
            role = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
            if role is not None and str(_g(role, "scope") or "") == "admin":
                return True
        return False

    async def is_platform_admin(self, user_id: str) -> bool:
        """平台管理面资格：预置 platform_admin，或任意 admin 类型角色。"""
        role_ids = await self.role_ids_for_user(user_id)
        if PLATFORM_ADMIN_ROLE_ID in role_ids:
            return True
        return await self.has_admin_scope_role(user_id)

    async def has_manager_access(self, user_id: str) -> bool:
        """能否进入管理面：平台管理员，或持有任一产品权限码。"""
        if await self.is_platform_admin(user_id):
            return True
        permissions = await self.permissions_for_user(user_id)
        return any(
            permission.endswith((":read", ":write", ":act", ":assign"))
            for permission in permissions
        )

    async def _permission_ids_for_role(self, role_id: str) -> list[str]:
        rows = await self._h.list_records(
            _ROLE_PERMISSION_TABLE, {"role_id": role_id}, limit=_CAP, offset=0
        )
        return sorted(str(_g(row, "permission_id")) for row in rows)

    async def _user_ids_for_role(self, role_id: str) -> list[str]:
        rows = await self._h.list_records(
            _ROLE_USER_TABLE, {"role_id": role_id}, limit=_CAP, offset=0
        )
        return sorted(str(_g(row, "user_id")) for row in rows if _active_assignment(row))

    async def _role_out(self, row: Any) -> dict[str, Any]:
        role_id = str(_g(row, "role_id"))
        user_ids = await self._user_ids_for_role(role_id)
        return {
            "role_id": role_id,
            "name": _g(row, "name"),
            "description": _g(row, "description"),
            "scope": _g(row, "scope"),
            "is_system": bool(_g(row, "is_system", False)),
            "enabled": bool(_g(row, "enabled", True)),
            "permission_ids": await self._permission_ids_for_role(role_id),
            "user_ids": user_ids,
            "assignee_count": len(user_ids),
            "created_at": iso_datetime(_g(row, "created_at")),
            "updated_at": iso_datetime(_g(row, "updated_at")),
        }

    async def list_roles(self, query: RoleListQuery | None = None) -> dict[str, Any]:
        """筛选、搜索、排序、分页均在服务端完成。"""
        query = query or RoleListQuery()
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        if query.scope is not None:
            filters["scope"] = query.scope

        rows = await self._h.list_records(_ROLE_TABLE, filters, limit=_CAP, offset=0)
        search_query = (query.search or "").strip()
        if search_query:
            rows = [row for row in rows if _matches_role_search(row, search_query)]

        items = [await self._role_out(row) for row in rows]
        _sort_role_items(items, query.sort_by, query.sort_order)

        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        total = len(items)
        offset = (page - 1) * page_size
        return {
            "items": items[offset:offset + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_role(self, role_id: str) -> dict[str, Any] | None:
        row = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
        return None if row is None else await self._role_out(row)

    async def list_role_users(
        self,
        role_id: str,
        query: RoleUserListQuery | None = None,
    ) -> dict[str, Any] | None:
        """列出角色已授权用户；筛选、搜索、排序、分页均在服务端完成。"""
        role = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
        if role is None:
            return None

        query = query or RoleUserListQuery()
        rows = await self._h.list_records(
            _ROLE_USER_TABLE, {"role_id": role_id}, limit=_CAP, offset=0
        )
        search_query = (query.search or "").strip()
        if search_query:
            rows = [row for row in rows if _matches_role_user_search(row, search_query)]

        items = [_role_user_out(row) for row in rows]
        _sort_role_user_items(items, query.sort_by, query.sort_order)

        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        total = len(items)
        offset = (page - 1) * page_size
        return {
            "items": items[offset:offset + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def _validate_permission_ids(self, scope: str, permission_ids: list[str]) -> list[str]:
        unique = sorted(set(permission_ids))
        for permission_id in unique:
            row = await self._h.get(_PERMISSION_TABLE, {"permission_id": permission_id})
            if row is None or not bool(_g(row, "enabled", True)):
                raise ValueError(f"permission not found or disabled: {permission_id}")
            if _g(row, "scope") != scope:
                raise ValueError(f"permission scope mismatch: {permission_id}")
        return unique

    async def _replace_permissions(
        self, role_id: str, permission_ids: list[str], operator_id: str
    ) -> None:
        existing = await self._h.list_records(
            _ROLE_PERMISSION_TABLE, {"role_id": role_id}, limit=_CAP, offset=0
        )
        existing_ids = {str(_g(row, "permission_id")) for row in existing}
        target_ids = set(permission_ids)
        for permission_id in existing_ids - target_ids:
            await self._h.delete(
                _ROLE_PERMISSION_TABLE,
                {"role_id": role_id, "permission_id": permission_id},
            )
        now = utc_now()
        for permission_id in target_ids - existing_ids:
            await self._h.create(
                _ROLE_PERMISSION_TABLE,
                {
                    "role_id": role_id,
                    "permission_id": permission_id,
                    "data": None,
                    "created_at": now,
                    "created_by": operator_id,
                    "updated_at": now,
                    "updated_by": operator_id,
                },
            )

    async def create_role(
        self,
        *,
        role_id: str,
        name: str,
        description: str | None,
        scope: str,
        permission_ids: list[str],
        operator_id: str,
    ) -> dict[str, Any]:
        role_id = role_id.strip()
        if not _ROLE_ID_RE.fullmatch(role_id):
            raise ValueError("role_id must use letters, digits, underscore or hyphen")
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        if len(name) > 128:
            raise ValueError("name must be at most 128 characters")
        if scope not in VALID_SCOPES:
            raise ValueError(f"scope must be one of {sorted(VALID_SCOPES)}")
        if await self._h.get(_ROLE_TABLE, {"role_id": role_id}) is not None:
            raise ValueError("role_id already exists")
        permissions = await self._validate_permission_ids(scope, permission_ids)
        now = utc_now()
        await self._h.create(
            _ROLE_TABLE,
            {
                "role_id": role_id,
                "name": name,
                "description": strip_optional(description),
                "scope": scope,
                "is_system": False,
                "enabled": True,
                "data": None,
                "created_at": now,
                "created_by": operator_id,
                "updated_at": now,
                "updated_by": operator_id,
            },
        )
        await self._replace_permissions(role_id, permissions, operator_id)
        return (await self.get_role(role_id)) or {}

    async def update_role(
        self,
        role_id: str,
        *,
        name: str | None,
        description: str | None,
        scope: str | None,
        enabled: bool | None,
        permission_ids: list[str] | None,
        operator_id: str,
    ) -> dict[str, Any] | None:
        row = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
        if row is None:
            return None
        is_system = bool(_g(row, "is_system", False))
        if is_system and permission_ids is not None:
            raise ValueError("system role permissions cannot be modified")
        if is_system and enabled is not None:
            raise ValueError("system role enabled cannot be modified")
        updates: dict[str, Any] = {"updated_at": utc_now(), "updated_by": operator_id}
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("name is required")
            if len(name) > 128:
                raise ValueError("name must be at most 128 characters")
            updates["name"] = name
        if description is not None:
            updates["description"] = strip_optional(description)
        target_scope = str(_g(row, "scope"))
        if scope is not None:
            if scope not in VALID_SCOPES:
                raise ValueError(f"scope must be one of {sorted(VALID_SCOPES)}")
            target_scope = scope
            updates["scope"] = scope
        if enabled is not None:
            updates["enabled"] = bool(enabled)
        if permission_ids is not None:
            permissions = await self._validate_permission_ids(target_scope, permission_ids)
            await self._replace_permissions(role_id, permissions, operator_id)
        elif scope is not None and scope != _g(row, "scope"):
            await self._validate_permission_ids(
                target_scope,
                await self._permission_ids_for_role(role_id),
            )
        await self._h.update(_ROLE_TABLE, {"role_id": role_id}, updates)
        return await self.get_role(role_id)

    async def delete_role(self, role_id: str, operator_id: str) -> bool:
        row = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
        if row is None:
            return False
        if bool(_g(row, "is_system", False)):
            raise ValueError("system role cannot be deleted")
        await self._h.delete(_ROLE_PERMISSION_TABLE, {"role_id": role_id})
        await self._h.delete(_ROLE_USER_TABLE, {"role_id": role_id})
        deleted = await self._h.delete(_ROLE_TABLE, {"role_id": role_id})
        if deleted:
            _log.info(
                "authz role deleted operator_id=%s role_id=%s",
                operator_id,
                role_id,
            )
        return deleted

    async def assign_role_users(
        self,
        role_id: str,
        user_ids: list[str],
        operator_id: str,
        expires_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """批量为角色新增或更新用户授权，共享同一过期时间。"""
        role = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
        if role is None:
            return None

        target_ids = sorted({
            str(user_id).strip()
            for user_id in user_ids
            if str(user_id).strip()
        })
        if not target_ids:
            raise ValueError("user_ids is required")

        normalized_expires = expires_at
        if normalized_expires is not None:
            if normalized_expires.tzinfo is None:
                normalized_expires = normalized_expires.replace(tzinfo=UTC)
            if normalized_expires <= utc_now():
                raise ValueError("expires_at must be in the future")

        now = utc_now()
        created_ids: list[str] = []
        updated_ids: list[str] = []
        for target_user_id in target_ids:
            filters = {"role_id": role_id, "user_id": target_user_id}
            existing = await self._h.get(_ROLE_USER_TABLE, filters)
            if existing is None:
                await self._h.create(
                    _ROLE_USER_TABLE,
                    {
                        **filters,
                        "granted_by": operator_id,
                        "expires_at": normalized_expires,
                        "data": None,
                        "created_at": now,
                        "created_by": operator_id,
                        "updated_at": now,
                        "updated_by": operator_id,
                    },
                )
                created_ids.append(target_user_id)
            else:
                await self._h.update(
                    _ROLE_USER_TABLE,
                    filters,
                    {
                        "granted_by": operator_id,
                        "expires_at": normalized_expires,
                        "updated_at": now,
                        "updated_by": operator_id,
                    },
                )
                updated_ids.append(target_user_id)

        _log.info(
            "authz role assignment changed operator_id=%s role_id=%s "
            "created_user_ids=%s updated_user_ids=%s expires_at=%s",
            operator_id,
            role_id,
            created_ids,
            updated_ids,
            iso_datetime(normalized_expires),
        )
        return await self.get_role(role_id)

    async def replace_role_users(
        self, role_id: str, user_ids: list[str], operator_id: str
    ) -> dict[str, Any] | None:
        role = await self._h.get(_ROLE_TABLE, {"role_id": role_id})
        if role is None:
            return None
        target_ids = {str(user_id).strip() for user_id in user_ids if str(user_id).strip()}
        existing = await self._h.list_records(
            _ROLE_USER_TABLE, {"role_id": role_id}, limit=_CAP, offset=0
        )
        existing_ids = {str(_g(row, "user_id")) for row in existing}
        for user_id in existing_ids - target_ids:
            await self._h.delete(_ROLE_USER_TABLE, {"role_id": role_id, "user_id": user_id})
        now = utc_now()
        for user_id in target_ids - existing_ids:
            await self._h.create(
                _ROLE_USER_TABLE,
                {
                    "role_id": role_id,
                    "user_id": user_id,
                    "granted_by": operator_id,
                    "expires_at": None,
                    "data": None,
                    "created_at": now,
                    "created_by": operator_id,
                    "updated_at": now,
                    "updated_by": operator_id,
                },
            )
        # This API represents a complete, non-expiring assignment list.  If a
        # previously expiring assignment is selected again, make it effective
        # indefinitely instead of silently leaving an expired row in place.
        reactivated_ids: set[str] = set()
        for user_id in target_ids & existing_ids:
            row = next(item for item in existing if str(_g(item, "user_id")) == user_id)
            if _g(row, "expires_at") is not None:
                await self._h.update(
                    _ROLE_USER_TABLE,
                    {"role_id": role_id, "user_id": user_id},
                    {
                        "granted_by": operator_id,
                        "expires_at": None,
                        "updated_at": now,
                        "updated_by": operator_id,
                    },
                )
                reactivated_ids.add(user_id)
        if existing_ids != target_ids or reactivated_ids:
            _log.info(
                "authz role assignment changed operator_id=%s role_id=%s "
                "before_user_ids=%s after_user_ids=%s reactivated_user_ids=%s",
                operator_id,
                role_id,
                sorted(existing_ids),
                sorted(target_ids),
                sorted(reactivated_ids),
            )
        return await self.get_role(role_id)
