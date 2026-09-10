"""实例服务资源授权 instance_service_resource 业务逻辑（授权即实例化）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.service_config_template import ServiceConfigTemplateService
from manager_server.core.instance_access import auto_bind_from_match_expr
from manager_server.core.instance_resource.runtime_config_sync import sync_runtime_config
from manager_server.core.template.push_template_to_runtime import (
    record_service_template_ref_on_runtime,
    unrecord_service_template_ref_on_runtime,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.match_expr import canonicalize_match_expr
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, strip_optional, utc_now
from manager_server.models.instance_resource_models import INSTANCE_SERVICE_RESOURCE_TABLE_DEF
from manager_server.models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF

_log = get_logger(__name__)
_GRANT = INSTANCE_SERVICE_RESOURCE_TABLE_DEF.table_name
_SVC_TPL = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_CAP = 100_000
_ALLOWED_GRANT_SORT_FIELDS = frozenset(
    {
        "resource_id",
        "resource_name",
        "priority",
        "granted_by",
        "expires_at",
        "enabled",
        "updated_at",
        "ref_template_id",
    }
)


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


async def _delete_where(
    handler: DBHandler, table: str, filters: dict[str, Any], pk_field: str
) -> None:
    rows = await handler.list_records(table, filters, limit=_CAP, offset=0)
    for r in rows:
        await handler.delete(table, {pk_field: _g(r, pk_field)})


def grant_out(row: Any) -> dict[str, Any]:
    expr = _g(row, "match_expr")
    if expr is None:
        expr = []
    return {
        "id": _g(row, "id"),
        "jiuwenclaw_id": _g(row, "jiuwenclaw_id"),
        "resource_id": _g(row, "resource_id"),
        "resource_name": _g(row, "resource_name"),
        "resource_desc": _g(row, "resource_desc"),
        "ref_template_id": _g(row, "ref_template_id"),
        "match_expr": expr,
        "priority": int(_g(row, "priority", 0) or 0),
        "granted_by": _g(row, "granted_by"),
        "expires_at": iso_datetime(_g(row, "expires_at")),
        "enabled": bool(_g(row, "enabled", True)),
        "data": _g(row, "data"),
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


def match_key(expr: Any) -> str:
    return json.dumps(canonicalize_match_expr(expr), ensure_ascii=False, separators=(",", ":"))


class InstanceServiceResourceService:
    """实例服务资源授权（instance_service_resource）。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler
        self._tpl = ServiceConfigTemplateService(handler)

    async def list_grants(self, template_id: str, jiuwenclaw_id: str | None = None) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {"ref_template_id": template_id}
        if jiuwenclaw_id:
            filters["jiuwenclaw_id"] = jiuwenclaw_id
        rows = await self._h.list_records(_GRANT, filters, limit=_CAP, offset=0)
        return [grant_out(r) for r in rows]

    async def create_resource(
        self,
        jiuwenclaw_id: str,
        template_id: str,
        match_exprs: list[Any],
        *,
        resource_name: str | None = None,
        resource_desc: str | None = None,
        priority: int = 0,
        granted_by: str | None = None,
        enabled: bool = True,
        expires_at: datetime | None = None,
        data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not match_exprs:
            raise ValueError("match_exprs must not be empty")
        return await self._write_grants(
            jiuwenclaw_id,
            template_id,
            match_exprs,
            resource_id=None,
            resource_name=resource_name,
            resource_desc=resource_desc,
            priority=priority,
            granted_by=granted_by,
            enabled=enabled,
            expires_at=expires_at,
            data=data,
        )

    async def update_resource(
        self,
        jiuwenclaw_id: str,
        resource_id: str,
        match_exprs: list[Any],
        *,
        resource_name: str | None = None,
        resource_desc: str | None = None,
        priority: int = 0,
        granted_by: str | None = None,
        enabled: bool = True,
        expires_at: datetime | None = None,
        data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rid = str(resource_id).strip()
        if not rid:
            raise ValueError("resource_id is required")
        if not match_exprs:
            raise ValueError("match_exprs must not be empty")
        existing = await self._h.list_records(
            _GRANT,
            {"jiuwenclaw_id": jiuwenclaw_id, "resource_id": rid},
            limit=_CAP,
            offset=0,
        )
        if not existing:
            raise LookupError(f"instance service resource not found: {rid}")
        template_id = str(_g(existing[0], "ref_template_id") or "")
        return await self._write_grants(
            jiuwenclaw_id,
            template_id,
            match_exprs,
            resource_id=rid,
            resource_name=resource_name,
            resource_desc=resource_desc,
            priority=priority,
            granted_by=granted_by,
            enabled=enabled,
            expires_at=expires_at,
            data=data,
        )

    async def _list_instance_rows(self, jiuwenclaw_id: str) -> list[Any]:
        return await self._h.list_records(
            _GRANT,
            {"jiuwenclaw_id": jiuwenclaw_id},
            limit=_CAP,
            offset=0,
        )

    @staticmethod
    def _project_upsert(
        current: list[Any],
        resource_id: str,
        new_rows: list[dict[str, Any]],
    ) -> list[Any]:
        rid = str(resource_id).strip()
        kept = [row for row in current if str(_g(row, "resource_id") or "").strip() != rid]
        return [*kept, *new_rows]

    @staticmethod
    def _project_without(current: list[Any], resource_id: str) -> list[Any]:
        rid = str(resource_id).strip()
        return [row for row in current if str(_g(row, "resource_id") or "").strip() != rid]

    async def _write_grants(
        self,
        jiuwenclaw_id: str,
        template_id: str,
        match_exprs: list[Any],
        *,
        resource_id: str | None = None,
        resource_name: str | None = None,
        resource_desc: str | None = None,
        priority: int = 0,
        granted_by: str | None = None,
        enabled: bool = True,
        expires_at: datetime | None = None,
        data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if await self._tpl.get(template_id) is None:
            raise ValueError(f"service_config_template not found: {template_id}")
        normalized_resource_id = (resource_id or "").strip() or None
        target_filters: dict[str, Any] = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "ref_template_id": template_id,
        }
        existing: list[Any] = []
        if normalized_resource_id:
            target_filters["resource_id"] = normalized_resource_id
            existing = await self._h.list_records(_GRANT, target_filters, limit=_CAP, offset=0)
        resolved_resource_id = normalized_resource_id or new_uuid4()
        existing_name = str(_g(existing[0], "resource_name") or "").strip() if existing else ""
        existing_desc = str(_g(existing[0], "resource_desc") or "").strip() if existing else ""
        resolved_name = (resource_name or "").strip() or existing_name or None
        resolved_desc = (resource_desc or "").strip() or existing_desc or None
        if not resolved_name:
            raise ValueError("resource_name is required")

        seen: set[str] = set()
        grant_rows: list[dict[str, Any]] = []
        now = utc_now()
        granted_by_norm = strip_optional(granted_by)
        for raw in match_exprs:
            expr = canonicalize_match_expr(raw)
            key = match_key(expr)
            if key in seen:
                continue
            seen.add(key)
            grant_rows.append(
                {
                    "jiuwenclaw_id": jiuwenclaw_id,
                    "resource_id": resolved_resource_id,
                    "resource_name": resolved_name,
                    "resource_desc": resolved_desc,
                    "ref_template_id": template_id,
                    "match_expr": expr,
                    "priority": int(priority),
                    "granted_by": granted_by_norm,
                    "expires_at": expires_at,
                    "enabled": enabled,
                    "data": data,
                    "created_at": now,
                    "updated_at": now,
                }
            )

        # 先 sync Runtime（目标态），成功后再写 Manager / 引用索引
        current = await self._list_instance_rows(jiuwenclaw_id)
        before_resource_ids: set[str] = set()
        for r in current:
            if str(_g(r, "ref_template_id") or "").strip() != template_id:
                continue
            rid = str(_g(r, "resource_id") or "").strip()
            if rid:
                before_resource_ids.add(rid)
        is_new_resource = resolved_resource_id not in before_resource_ids
        projected = self._project_upsert(current, resolved_resource_id, grant_rows)
        try:
            await sync_runtime_config(
                self._h, jiuwenclaw_id, resource_rows=projected
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to runtime: {exc}") from exc

        if normalized_resource_id:
            await _delete_where(self._h, _GRANT, target_filters, "id")
        for row in grant_rows:
            await self._h.create(_GRANT, row)
            await auto_bind_from_match_expr(self._h, jiuwenclaw_id, row["match_expr"])
        if is_new_resource:
            await record_service_template_ref_on_runtime(
                self._h, jiuwenclaw_id, template_id
            )
        _log.info(
            "[InstanceResource] instance_service_resource.write",
            jiuwenclaw_id=jiuwenclaw_id,
            template_id=template_id,
            resource_id=resolved_resource_id,
            n=len(seen),
        )
        return await self.list_grants(template_id, jiuwenclaw_id)

    async def remove_resource(self, jiuwenclaw_id: str, resource_id: str) -> bool:
        rid = str(resource_id).strip()
        if not rid:
            return False
        filters = {"jiuwenclaw_id": jiuwenclaw_id, "resource_id": rid}
        rows = await self._h.list_records(_GRANT, filters, limit=_CAP, offset=0)
        if not rows:
            return False

        current = await self._list_instance_rows(jiuwenclaw_id)
        projected = self._project_without(current, rid)
        ref_template_id = str(_g(rows[0], "ref_template_id") or "").strip()
        try:
            await sync_runtime_config(
                self._h, jiuwenclaw_id, resource_rows=projected
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to runtime: {exc}") from exc

        for r in rows:
            await self._h.delete(_GRANT, {"id": _g(r, "id")})
        if ref_template_id:
            await unrecord_service_template_ref_on_runtime(
                self._h, jiuwenclaw_id, ref_template_id
            )
        _log.info(
            "[InstanceResource] instance_service_resource.remove",
            jiuwenclaw_id=jiuwenclaw_id,
            resource_id=rid,
            rows=len(rows),
        )
        return True

    async def remove_from_instance(
        self, jiuwenclaw_id: str, template_id: str, *, resource_id: str | None = None
    ) -> bool:
        filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id, "ref_template_id": template_id}
        if resource_id:
            filters["resource_id"] = resource_id
        rows = await self._h.list_records(_GRANT, filters, limit=_CAP, offset=0)
        if not rows:
            return False

        remove_ids = {
            str(_g(r, "resource_id") or "").strip()
            for r in rows
            if _g(r, "resource_id")
        }
        # 每个 resource 对其 ref_template_id 贡献一条索引；按 (resource, template) 去重后扣减。
        unrecord_pairs = {
            (
                str(_g(r, "resource_id") or "").strip(),
                str(_g(r, "ref_template_id") or "").strip(),
            )
            for r in rows
            if _g(r, "resource_id") and _g(r, "ref_template_id")
        }
        current = await self._list_instance_rows(jiuwenclaw_id)
        projected = [
            row
            for row in current
            if str(_g(row, "resource_id") or "").strip() not in remove_ids
        ]
        try:
            await sync_runtime_config(
                self._h, jiuwenclaw_id, resource_rows=projected
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to runtime: {exc}") from exc

        for r in rows:
            await self._h.delete(_GRANT, {"id": _g(r, "id")})
        for _rid, tid in sorted(unrecord_pairs):
            await unrecord_service_template_ref_on_runtime(
                self._h, jiuwenclaw_id, tid
            )
        _log.info(
            "[InstanceResource] instance_service_resource.remove_from_instance",
            jiuwenclaw_id=jiuwenclaw_id,
            template_id=template_id,
            resource_id=resource_id,
            rows=len(rows),
        )
        return True

    async def delete_by_template(self, template_id: str) -> None:
        await _delete_where(self._h, _GRANT, {"ref_template_id": template_id}, "id")

    async def list_instance_resources(
        self,
        jiuwenclaw_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        enabled: bool | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> dict[str, Any]:
        """按 ``instance_service_resource`` 行返回（一 resource_id 一行，对齐表结构）。

        ``template_name`` 仅用于 search / sort，不写入响应体。
        """
        requested_sort = (sort_by or "").strip().lower()
        grant_sort_field = (
            "ref_template_id" if requested_sort == "template_name" else requested_sort
        )
        order_by = resolve_order_by(
            grant_sort_field,
            sort_order,
            allowed_sort_fields=_ALLOWED_GRANT_SORT_FIELDS,
        )
        rows = await self._h.list_records(
            _GRANT,
            {"jiuwenclaw_id": jiuwenclaw_id},
            limit=_CAP,
            offset=0,
            order_by=order_by,
        )
        items = [grant_out(r) for r in rows]

        tpl_name_by_id: dict[str, str] = {}
        need_tpl_names = bool((search or "").strip()) or requested_sort == "template_name"
        if need_tpl_names:
            tpl_rows = await self._h.list_records(_SVC_TPL, {}, limit=_CAP, offset=0)
            tpl_name_by_id = {
                str(_g(r, "template_id") or ""): str(_g(r, "template_name") or "")
                for r in tpl_rows
                if _g(r, "template_id")
            }

        if enabled is not None:
            items = [x for x in items if bool(x.get("enabled", True)) is enabled]

        kw = (search or "").strip().lower()
        if kw:
            matched: list[dict[str, Any]] = []
            for x in items:
                tid = str(x.get("ref_template_id") or "")
                parts = [
                    str(x.get("resource_id") or ""),
                    str(x.get("resource_name") or ""),
                    str(x.get("resource_desc") or ""),
                    tid,
                    tpl_name_by_id.get(tid, ""),
                    str(x.get("granted_by") or ""),
                    str(x.get("priority") or ""),
                ]
                if any(kw in s.lower() for s in parts):
                    matched.append(x)
            items = matched

        if requested_sort == "template_name":
            reverse = (sort_order or "asc").strip().lower() == "desc"
            items.sort(
                key=lambda x: tpl_name_by_id.get(
                    str(x.get("ref_template_id") or ""), ""
                ).lower(),
                reverse=reverse,
            )
        elif requested_sort == "priority":
            reverse = (sort_order or "asc").strip().lower() == "desc"
            items.sort(
                key=lambda x: int(x.get("priority") or 0),
                reverse=reverse,
            )
        elif requested_sort == "resource_name":
            reverse = (sort_order or "asc").strip().lower() == "desc"
            items.sort(
                key=lambda x: str(x.get("resource_name") or "").lower(),
                reverse=reverse,
            )

        total = len(items)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        offset = (page - 1) * page_size
        items = items[offset:offset + page_size]
        return {"items": items, "total": total, "page": page, "page_size": page_size}
