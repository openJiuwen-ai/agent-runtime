"""Agent 模板 agent_template 业务逻辑（平台全局目录）。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.template_ref import read_template_ref_from_row
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, strip_optional, utc_now
from manager_server.models.template_models import AGENT_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_schemas import (
    AgentTemplateCreateBody,
    AgentTemplateListQuery,
    AgentTemplateUpdateBody,
)

_log = get_logger(__name__)
_AGENT_TPL = AGENT_TEMPLATE_TABLE_DEF.table_name
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "template_id",
    "updated_at",
})


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    tags = _g(row, "agent_tags") or []
    if tags is not None and not isinstance(tags, list):
        tags = list(tags) if tags else []
    fields = [
        str(_g(row, "template_id", "") or ""),
        str(_g(row, "template_name", "") or ""),
        str(_g(row, "description", "") or ""),
        *(str(tag) for tag in tags),
    ]
    return any(needle in field.lower() for field in fields)


def agent_template_out(row: Any) -> dict[str, Any]:
    tags = _g(row, "agent_tags")
    if tags is not None and not isinstance(tags, list):
        tags = list(tags) if tags else None
    return {
        "id": _g(row, "id"),
        "template_id": _g(row, "template_id"),
        "template_name": _g(row, "template_name"),
        "description": _g(row, "description"),
        "agent_tags": tags,
        "template_ref": read_template_ref_from_row(row),
        "enabled": bool(_g(row, "enabled", True)),
        "data": _g(row, "data"),
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


# 与其它模板模块对齐，供 push_template_to_gateway 通过 row_to_out 解析。
row_to_out = agent_template_out


class AgentTemplateService:
    """Agent 模板目录 CRUD。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list(self, query: AgentTemplateListQuery) -> dict[str, Any]:
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
            rows = await self._h.list_records(
                _AGENT_TPL, filters, limit=10_000, offset=0, order_by=order_by
            )
            items = [
                agent_template_out(row)
                for row in rows
                if _matches_search(row, search_query)
            ]
            total = len(items)
            offset = (page - 1) * page_size
            page_items = items[offset:offset + page_size]
            return {"items": page_items, "total": total, "page": page, "page_size": page_size}

        offset = (page - 1) * page_size
        rows = await self._h.list_records(
            _AGENT_TPL, filters, limit=page_size, offset=offset, order_by=order_by
        )
        total = await self._h.count_records(_AGENT_TPL, filters)
        return {
            "items": [agent_template_out(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get(self, template_id: str) -> dict[str, Any] | None:
        row = await self._h.get(_AGENT_TPL, {"template_id": template_id})
        if row is None:
            return None
        return agent_template_out(row)

    async def create(self, body: AgentTemplateCreateBody) -> dict[str, Any]:
        template_id = new_uuid4()
        now = utc_now()
        await self._h.create(
            _AGENT_TPL,
            {
                "template_id": template_id,
                "template_name": body.template_name,
                "description": strip_optional(body.description),
                "agent_tags": body.agent_tags,
                "template_ref": body.template_ref or {},
                "enabled": body.enabled,
                "data": body.data,
                "created_at": now,
                "updated_at": now,
            },
        )
        _log.info("[Template] agent_template.create", template_id=template_id, template_name=body.template_name)
        created = await self.get(template_id)
        if created is None:  # pragma: no cover
            raise RuntimeError(f"agent_template just created but missing: {template_id}")
        return created

    async def update(self, template_id: str, body: AgentTemplateUpdateBody) -> dict[str, Any] | None:
        existing = await self._h.get(_AGENT_TPL, {"template_id": template_id})
        if existing is None:
            return None
        updates: dict[str, Any] = {}
        if body.template_name is not None:
            updates["template_name"] = body.template_name
        if body.description is not None:
            updates["description"] = strip_optional(body.description)
        if body.agent_tags is not None:
            updates["agent_tags"] = body.agent_tags
        if body.template_ref is not None:
            updates["template_ref"] = body.template_ref
        if body.enabled is not None:
            updates["enabled"] = body.enabled
        if body.data is not None:
            updates["data"] = body.data

        if not updates:
            return agent_template_out(existing)

        from manager_server.core.template.push_template_to_gateway import (
            AGENT_TEMPLATES_KIND,
            update_template_on_referencing_gateways,
        )

        # 先推 Gateway，成功后再写 Manager
        await update_template_on_referencing_gateways(
            self._h,
            AGENT_TEMPLATES_KIND,
            template_id,
            updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        await self._h.update(_AGENT_TPL, {"template_id": template_id}, payload)
        return await self.get(template_id)

    async def delete(self, template_id: str) -> bool:
        if await self._h.get(_AGENT_TPL, {"template_id": template_id}) is None:
            return False
        from manager_server.core.instance_resource import InstanceAgentResourceService

        # 先清实例授权（remove_resource 路径会卸 Gateway；此处按模板批量清 MDB 行）。
        await InstanceAgentResourceService(self._h).delete_by_template(template_id)
        await self._h.delete(_AGENT_TPL, {"template_id": template_id})
        _log.info("[Template] agent_template.delete", template_id=template_id)
        return True

    async def exists(self, template_id: str) -> bool:
        return await self._h.get(_AGENT_TPL, {"template_id": template_id}) is not None

    async def get_row(self, template_id: str) -> Any | None:
        return await self._h.get(_AGENT_TPL, {"template_id": template_id})
