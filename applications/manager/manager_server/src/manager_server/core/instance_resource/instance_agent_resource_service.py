"""实例 Agent 授权 instance_agent_resource 业务逻辑（授权即实例化）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.agent_template import AgentTemplateService, agent_template_out
from manager_server.core.template.push_agent_template_to_gateway import (
    build_agent_resource_gateway_payload,
    delete_agent_resource_from_gateway,
    sync_agent_resource_to_gateway,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.match_expr import (
    canonicalize_match_expr,
)
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, strip_optional, utc_now
from manager_server.core.instance_access import auto_bind_from_match_expr
from manager_server.models.instance_resource_models import INSTANCE_AGENT_RESOURCE_TABLE_DEF
from manager_server.models.template_models import AGENT_TEMPLATE_TABLE_DEF

_log = get_logger(__name__)
_GRANT = INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name
_AGENT_TPL = AGENT_TEMPLATE_TABLE_DEF.table_name
_CAP = 100_000
_ALLOWED_GRANT_SORT_FIELDS = frozenset(
    {"resource_id", "granted_by", "expires_at", "enabled", "updated_at", "ref_template_id"}
)
_ALLOWED_TEMPLATE_SORT_FIELDS = frozenset({"template_name", "updated_at", "description", "template_id"})


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
        "granted_by": _g(row, "granted_by"),
        "expires_at": iso_datetime(_g(row, "expires_at")),
        "enabled": bool(_g(row, "enabled", True)),
        "data": _g(row, "data"),
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


def match_key(expr: Any) -> str:
    return json.dumps(canonicalize_match_expr(expr), ensure_ascii=False, separators=(",", ":"))


def grant_expired(expires_at: Any) -> bool:
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= utc_now()


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _dt_sort_value(value: Any) -> float:
    dt = _parse_dt(value)
    if dt is None:
        return float("-inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class InstanceAgentResourceService:
    """实例 Agent 授权（instance_agent_resource）。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler
        self._tpl = AgentTemplateService(handler)

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
            raise LookupError(f"instance agent resource not found: {rid}")
        template_id = str(_g(existing[0], "ref_template_id") or "")
        return await self._write_grants(
            jiuwenclaw_id,
            template_id,
            match_exprs,
            resource_id=rid,
            resource_name=resource_name,
            resource_desc=resource_desc,
            granted_by=granted_by,
            enabled=enabled,
            expires_at=expires_at,
            data=data,
        )

    async def _write_grants(
        self,
        jiuwenclaw_id: str,
        template_id: str,
        match_exprs: list[Any],
        *,
        resource_id: str | None = None,
        resource_name: str | None = None,
        resource_desc: str | None = None,
        granted_by: str | None = None,
        enabled: bool = True,
        expires_at: datetime | None = None,
        data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not await self._tpl.exists(template_id):
            raise ValueError(f"agent_template not found: {template_id}")
        normalized_resource_id = (resource_id or "").strip() or None
        before_template_refs = await self._h.list_records(
            _GRANT,
            {"jiuwenclaw_id": jiuwenclaw_id, "ref_template_id": template_id},
            limit=_CAP,
            offset=0,
        )
        before_resource_ids = {
            str(_g(r, "resource_id") or "").strip()
            for r in before_template_refs
            if _g(r, "resource_id")
        }
        was_first_for_template = len(before_resource_ids) == 0
        target_filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id, "ref_template_id": template_id}
        if normalized_resource_id:
            target_filters["resource_id"] = normalized_resource_id
        existing = await self._h.list_records(_GRANT, target_filters, limit=_CAP, offset=0)
        resolved_resource_id = normalized_resource_id or new_uuid4()
        existing_name = str(_g(existing[0], "resource_name") or "").strip() if existing else ""
        existing_desc = str(_g(existing[0], "resource_desc") or "").strip() if existing else ""
        resolved_name = (resource_name or "").strip() or existing_name or None
        resolved_desc = (resource_desc or "").strip() or existing_desc or None

        seen: set[str] = set()
        grant_rows: list[dict[str, Any]] = []
        gateway_grants: list[dict[str, Any]] = []
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
                    "granted_by": granted_by_norm,
                    "expires_at": expires_at,
                    "enabled": enabled,
                    "data": data,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            gateway_grants.append(
                {
                    "match_expr": expr if expr is not None else [],
                    "granted_by": granted_by_norm,
                    "enabled": enabled,
                    "expires_at": iso_datetime(expires_at),
                    "data": data,
                }
            )

        # 先推 Gateway，成功后再写 Manager，避免 Manager 有数据而 Gateway 缺失
        await sync_agent_resource_to_gateway(
            self._h,
            jiuwenclaw_id,
            resolved_resource_id,
            template_id,
            was_first_for_template=was_first_for_template,
            resource_payload=build_agent_resource_gateway_payload(
                resource_id=resolved_resource_id,
                ref_template_id=template_id,
                resource_name=resolved_name,
                resource_desc=resolved_desc,
                grants=gateway_grants,
            ),
        )

        if normalized_resource_id:
            await _delete_where(self._h, _GRANT, target_filters, "id")
        for row in grant_rows:
            await self._h.create(_GRANT, row)
            await auto_bind_from_match_expr(self._h, jiuwenclaw_id, row["match_expr"])
        _log.info(
            "[InstanceResource] instance_agent_resource.write",
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
        ref_template_id = str(_g(rows[0], "ref_template_id") or "")
        remaining_after = 0
        if ref_template_id:
            sibling_rows = await self._h.list_records(
                _GRANT,
                {"jiuwenclaw_id": jiuwenclaw_id, "ref_template_id": ref_template_id},
                limit=_CAP,
                offset=0,
            )
            remaining_ids = {
                str(_g(r, "resource_id") or "").strip()
                for r in sibling_rows
                if _g(r, "resource_id")
            }
            remaining_ids.discard(rid)
            remaining_after = len(remaining_ids)

        await delete_agent_resource_from_gateway(
            self._h,
            jiuwenclaw_id,
            rid,
            ref_template_id=ref_template_id,
            remaining_resources_after=remaining_after,
        )
        for r in rows:
            await self._h.delete(_GRANT, {"id": _g(r, "id")})
        _log.info(
            "[InstanceResource] instance_agent_resource.remove",
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
        grouped: dict[str, str] = {}
        for r in rows:
            rid = str(_g(r, "resource_id") or "").strip()
            if rid:
                grouped[rid] = str(_g(r, "ref_template_id") or "")

        # 按模板统计删除前的 resource 集合，推 Gateway 时传入删除后剩余数
        all_for_template = await self._h.list_records(
            _GRANT,
            {"jiuwenclaw_id": jiuwenclaw_id, "ref_template_id": template_id},
            limit=_CAP,
            offset=0,
        )
        all_ids = {
            str(_g(r, "resource_id") or "").strip()
            for r in all_for_template
            if _g(r, "resource_id")
        }
        for rid, ref_template_id in grouped.items():
            remaining_ids = set(all_ids)
            remaining_ids.discard(rid)
            await delete_agent_resource_from_gateway(
                self._h,
                jiuwenclaw_id,
                rid,
                ref_template_id=ref_template_id,
                remaining_resources_after=len(remaining_ids),
            )
            all_ids.discard(rid)

        for r in rows:
            await self._h.delete(_GRANT, {"id": _g(r, "id")})
        _log.info(
            "[InstanceResource] instance_agent_resource.remove_from_instance",
            jiuwenclaw_id=jiuwenclaw_id,
            template_id=template_id,
            resource_id=resource_id,
            rows=len(rows),
        )
        return len(rows) > 0

    async def delete_by_template(self, template_id: str) -> None:
        await _delete_where(self._h, _GRANT, {"ref_template_id": template_id}, "id")

    async def list_instance_agent_resources(
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
        requested_sort = (sort_by or "").strip().lower()
        grant_sort_field = "ref_template_id" if requested_sort == "template_name" else requested_sort
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
        by_agent: dict[str, dict[str, Any]] = {}
        for r in rows:
            aid = str(_g(r, "resource_id"))
            tid = str(_g(r, "ref_template_id"))
            bucket = by_agent.setdefault(aid, {"template_id": tid, "records": [], "primary": None})
            out = grant_out(r)
            bucket["records"].append(out)
            prev = bucket["primary"]
            if prev is None:
                bucket["primary"] = out
            else:
                prev_ts = _dt_sort_value(prev.get("updated_at"))
                curr_ts = _dt_sort_value(out.get("updated_at"))
                if curr_ts >= prev_ts:
                    bucket["primary"] = out
        out: list[dict[str, Any]] = []
        for aid, bucket in by_agent.items():
            tid = str(bucket["template_id"])
            b = await self._tpl.get_row(tid)
            if b is None:
                continue
            item = agent_template_out(b)
            item["resource_id"] = aid
            item["resource_name"] = (bucket["primary"] or {}).get("resource_name")
            item["resource_desc"] = (bucket["primary"] or {}).get("resource_desc")
            item["ref_template_id"] = tid
            item["records"] = bucket["records"]
            item["_primary_grant"] = bucket["primary"] or {}
            out.append(item)

        if enabled is not None:
            out = [x for x in out if bool((x.get("_primary_grant") or {}).get("enabled", True)) is enabled]

        kw = (search or "").strip().lower()
        if kw:
            matched: list[dict[str, Any]] = []
            for x in out:
                p = x.get("_primary_grant") or {}
                parts = [
                    str(x.get("resource_id") or ""),
                    str(p.get("resource_name") or ""),
                    str(p.get("resource_desc") or ""),
                    str(x.get("template_id") or ""),
                    str(x.get("template_name") or ""),
                    str(p.get("granted_by") or ""),
                ]
                if any(kw in s.lower() for s in parts):
                    matched.append(x)
            out = matched

        if requested_sort == "template_name":
            tpl_order = resolve_order_by(
                "template_name",
                sort_order,
                allowed_sort_fields=_ALLOWED_TEMPLATE_SORT_FIELDS,
            )
            tpl_rows = await self._h.list_records(_AGENT_TPL, {}, limit=_CAP, offset=0, order_by=tpl_order)
            by_tid: dict[str, list[dict[str, Any]]] = {}
            for item in out:
                by_tid.setdefault(str(item.get("template_id")), []).append(item)
            ordered: list[dict[str, Any]] = []
            for row in tpl_rows:
                tid = str(_g(row, "template_id"))
                matched = by_tid.get(tid) or []
                ordered.extend(matched)
            out = ordered

        total = len(out)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        offset = (page - 1) * page_size
        out = out[offset:offset + page_size]
        for x in out:
            x.pop("_primary_grant", None)
        return {"items": out, "total": total, "page": page, "page_size": page_size}
