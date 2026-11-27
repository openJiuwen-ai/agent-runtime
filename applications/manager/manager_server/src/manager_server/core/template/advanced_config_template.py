"""高级配置模板 advanced_config_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.push_template_to_gateway import (
    assert_template_deletable,
    assert_template_not_referenced,
    count_agent_template_reference_counts,
    delete_template_on_referencing_gateways,
    update_template_on_referencing_gateways,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.template_models import ADVANCED_CONFIG_TEMPLATE_TABLE_DEF
from manager_server.schemas.advanced_config_fields import (
    ADVANCED_CONFIG_BOOL_FIELDS,
    normalize_advanced_config_switches,
    validate_advanced_config_data,
)
from manager_server.schemas.template_schemas import (
    AdvancedConfigTemplateCreateBody,
    AdvancedConfigTemplateListQuery,
    AdvancedConfigTemplateOut,
    AdvancedConfigTemplateUpdateBody,
)
from manager_server.schemas.template_slot_schemas import ADVANCED_CONFIG_SLOT

_TABLE = ADVANCED_CONFIG_TEMPLATE_TABLE_DEF.table_name
_KIND = "advanced_config_templates"
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
    fields = [
        str(getattr(row, "template_id", "") or ""),
        str(getattr(row, "template_name", "") or ""),
        str(getattr(row, "description", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def row_to_out(row: Any, *, reference_count: int = 0) -> AdvancedConfigTemplateOut:
    return AdvancedConfigTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        enabled=row.enabled,
        reference_count=reference_count,
        progressive_tool_enabled=bool(row.progressive_tool_enabled),
        progressive_tool_optimize=bool(row.progressive_tool_optimize),
        a4p_enabled=bool(row.a4p_enabled),
        auto_memory_enabled=bool(row.auto_memory_enabled),
        enable_kv_cache_affinity=bool(row.enable_kv_cache_affinity),
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class AdvancedConfigTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _reference_counts(self) -> dict[str, int]:
        """被引用数：在 Agent 模板 advanced_config 槽位引用本类模板的 Agent 模板数。"""
        return await count_agent_template_reference_counts(
            self._handler, slot_keys=frozenset({ADVANCED_CONFIG_SLOT})
        )

    @staticmethod
    def _build_row_for_create(
        body: AdvancedConfigTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "enabled": body.enabled,
            "progressive_tool_enabled": body.progressive_tool_enabled,
            "progressive_tool_optimize": body.progressive_tool_optimize,
            "a4p_enabled": body.a4p_enabled,
            "auto_memory_enabled": body.auto_memory_enabled,
            "enable_kv_cache_affinity": body.enable_kv_cache_affinity,
            "data": validate_advanced_config_data(body.data),
        }

    async def create(
        self,
        body: AdvancedConfigTemplateCreateBody,
    ) -> AdvancedConfigTemplateOut:
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> AdvancedConfigTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

    async def list_templates(
        self,
        query: AdvancedConfigTemplateListQuery,
    ) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled

        reference_counts = await self._reference_counts()
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
                row_to_out(
                    r, reference_count=reference_counts.get(str(r.template_id), 0)
                ).model_dump(mode="json")
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
        items = [
            row_to_out(r, reference_count=reference_counts.get(str(r.template_id), 0)).model_dump(
                mode="json"
            )
            for r in rows
        ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update(
        self,
        template_id: str,
        body: AdvancedConfigTemplateUpdateBody,
    ) -> AdvancedConfigTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)
        bool_patch = {k: updates[k] for k in ADVANCED_CONFIG_BOOL_FIELDS if k in updates}
        if bool_patch:
            updates.update(normalize_advanced_config_switches(bool_patch))
        if "data" in updates:
            updates["data"] = validate_advanced_config_data(updates["data"])

        if not updates:
            row = await self._handler.get(_TABLE, {"template_id": template_id})
            if row is None:
                return None
            counts = await self._reference_counts()
            return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None

        if updates.get("enabled") is False and bool(getattr(existing, "enabled", True)):
            await assert_template_not_referenced(
                self._handler, template_id, _KIND, action="disable"
            )

        await update_template_on_referencing_gateways(
            self._handler,
            _KIND,
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
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return False
        await assert_template_deletable(self._handler, template_id, _KIND)
        await delete_template_on_referencing_gateways(
            self._handler,
            _KIND,
            template_id,
        )
        return await self._handler.delete(_TABLE, {"template_id": template_id})