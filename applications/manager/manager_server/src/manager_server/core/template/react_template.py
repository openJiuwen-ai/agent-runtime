"""React 运行策略模板 react_template 业务逻辑。

react 配置整段存 data JSON（body 作为传输格式，落库写入 data）。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.push_template_to_gateway import (
    assert_template_deletable,
    count_agent_template_reference_counts,
    delete_template_on_referencing_gateways,
    update_template_on_referencing_gateways,
)
from manager_server.core.template.compressor_model_resolver import (
    validate_react_model_refs,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.template_models import REACT_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_schemas import (
    ReactTemplateCreateBody,
    ReactTemplateListQuery,
    ReactTemplateOut,
    ReactTemplateUpdateBody,
)
from manager_server.schemas.template_slot_schemas import REACT_SLOT

_TABLE = REACT_TEMPLATE_TABLE_DEF.table_name
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


def row_to_out(row: Any, *, reference_count: int = 0) -> ReactTemplateOut:
    data = getattr(row, "data", None)
    body = data if isinstance(data, dict) else {}
    return ReactTemplateOut(
        id=getattr(row, "id", 0),
        template_id=str(getattr(row, "template_id", "") or ""),
        template_name=getattr(row, "template_name", "") or "",
        description=getattr(row, "description", None),
        enabled=bool(getattr(row, "enabled", True)),
        reference_count=reference_count,
        body=body,
        created_at=iso_datetime(getattr(row, "created_at", None)),
        updated_at=iso_datetime(getattr(row, "updated_at", None)),
    )


class ReactTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _reference_counts(self) -> dict[str, int]:
        """被引用数：在 Agent 模板 react 槽位引用本类模板的 Agent 模板数。"""
        return await count_agent_template_reference_counts(
            self._handler, slot_keys=frozenset({REACT_SLOT})
        )

    @staticmethod
    def _build_row_for_create(
        body: ReactTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "enabled": body.enabled,
            "data": body.body if isinstance(body.body, dict) else {},
        }

    async def create(self, body: ReactTemplateCreateBody) -> ReactTemplateOut:
        await validate_react_model_refs(self._handler, body.body)
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> ReactTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(getattr(row, "template_id", "")), 0))

    async def list_templates(
        self,
        query: ReactTemplateListQuery,
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
                    r, reference_count=reference_counts.get(str(getattr(r, "template_id", "")), 0)
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
            row_to_out(r, reference_count=reference_counts.get(str(getattr(r, "template_id", "")), 0)).model_dump(
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
        body: ReactTemplateUpdateBody,
    ) -> ReactTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)
        # body 为整段核心配置，不支持置空；显式传 null 或空 dict 视为未提供该字段。
        if not updates.get("body"):
            updates.pop("body", None)

        if not updates:
            row = await self._handler.get(_TABLE, {"template_id": template_id})
            if row is None:
                return None
            counts = await self._reference_counts()
            return row_to_out(row, reference_count=counts.get(str(getattr(row, "template_id", "")), 0))

        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None
        if "body" in updates and isinstance(updates["body"], dict):
            await validate_react_model_refs(self._handler, updates["body"])

        # 下发用 body 传输格式；MDB 写库映射到 data 列
        await update_template_on_referencing_gateways(
            self._handler,
            "react_templates",
            template_id,
            updates,
        )
        mdb_updates = dict(updates)
        body_value = mdb_updates.pop("body", None)
        if body_value is not None:
            mdb_updates["data"] = body_value
        mdb_updates["updated_at"] = utc_now()
        row = await self._handler.update(
            _TABLE, {"template_id": template_id}, mdb_updates
        )
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(getattr(row, "template_id", "")), 0))

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return False
        await assert_template_deletable(
            self._handler, template_id, "react_templates"
        )
        await delete_template_on_referencing_gateways(
            self._handler,
            "react_templates",
            template_id,
        )
        return await self._handler.delete(_TABLE, {"template_id": template_id})