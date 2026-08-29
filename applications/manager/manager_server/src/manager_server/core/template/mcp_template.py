"""MCP 模板 mcp_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.push_template_to_gateway import (
    assert_template_deletable,
    delete_template_on_referencing_gateways,
    update_template_on_referencing_gateways,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.template_models import MCP_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_schemas import (
    McpTemplateCreateBody,
    McpTemplateListQuery,
    McpTemplateOut,
    McpTemplateUpdateBody,
)

_TABLE = MCP_TEMPLATE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "updated_at",
})


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    mcp_entry = getattr(row, "mcp_entry", None)
    mcp_name = ""
    if isinstance(mcp_entry, dict):
        mcp_name = str(mcp_entry.get("name", "") or "")
    fields = [
        str(getattr(row, "template_id", "") or ""),
        str(getattr(row, "template_name", "") or ""),
        str(getattr(row, "description", "") or ""),
        mcp_name,
    ]
    return any(needle in field.lower() for field in fields)


def row_to_out(row: Any) -> McpTemplateOut:
    return McpTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        mcp_entry=row.mcp_entry,
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class McpTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    @staticmethod
    def _build_row_for_create(
        body: McpTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "mcp_entry": body.mcp_entry,
            "enabled": body.enabled,
            "data": body.data,
        }

    async def create(self, body: McpTemplateCreateBody) -> McpTemplateOut:
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_TABLE, payload)
        return row_to_out(created)
    async def get(self, template_id: str) -> McpTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        return row_to_out(row)

    async def list_templates(self, query: McpTemplateListQuery) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled

        order_by = resolve_order_by(
            query.sort_by, query.sort_order, allowed_sort_fields=_ALLOWED_SORT_FIELDS
        )
        search_query = (query.search or "").strip()
        if search_query:
            rows = await self._handler.list_records(
                _TABLE,
                filters,
                limit=_LIST_ALL_CAP,
                offset=0,
                order_by=order_by,
            )
            items = [
                row_to_out(r).model_dump(mode="json")
                for r in rows
                if _matches_search(r, search_query)
            ]
            total = len(items)
            offset = (page - 1) * page_size
            page_items = items[offset:offset + page_size]
            return {
                "items": page_items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _TABLE,
            filters,
            limit=page_size,
            offset=offset,
            order_by=order_by,
        )
        total = await self._handler.count_records(_TABLE, filters)
        items = [row_to_out(r).model_dump(mode="json") for r in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update(
        self,
        template_id: str,
        body: McpTemplateUpdateBody,
    ) -> McpTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(_TABLE, {"template_id": template_id})
            return row_to_out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None

        await update_template_on_referencing_gateways(
            self._handler,
            "mcp_templates",
            template_id,
            updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        row = await self._handler.update(
            _TABLE, {"template_id": template_id}, payload
        )
        if row is None:
            return None
        return row_to_out(row)

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return False
        await assert_template_deletable(self._handler, template_id, "mcp_templates")
        await delete_template_on_referencing_gateways(
            self._handler,
            "mcp_templates",
            template_id,
        )
        return await self._handler.delete(_TABLE, {"template_id": template_id})
