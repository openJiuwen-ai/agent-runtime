"""Agent 模板（agent_definition）目录查询与改名。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, utc_now
from manager_server.models.agent_definition_models import AGENT_DEFINITION_TABLE_DEF
from manager_server.schemas.agent_definition_schemas import AgentDefinitionListQuery

_TABLE = AGENT_DEFINITION_TABLE_DEF.table_name
_MAX_ROWS = 100_000
_ALLOWED_SORT_FIELDS = frozenset(
    {"agent_name", "agent_id", "created_at", "updated_at"}
)


class AgentNameDuplicated(Exception):
    """Agent Name 与某个有效行重名（大小写不敏感）。"""


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def agent_definition_out(row: Any) -> dict[str, Any]:
    return {
        "agent_id": str(_g(row, "agent_id") or ""),
        "agent_name": str(_g(row, "agent_name") or ""),
        "agent_config_template_id": str(_g(row, "agent_config_template_id") or ""),
        "agent_config_template_name": _g(row, "agent_config_template_name"),
        "runtime_template_id": str(_g(row, "runtime_template_id") or ""),
        "runtime_template_name": _g(row, "runtime_template_name"),
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


async def list_active_agent_definitions(handler: DBHandler) -> list[dict[str, Any]]:
    """取全部有效行（``deleted=0``），供用户面名称覆盖。"""
    rows = await handler.list_records(
        _TABLE, {"deleted": False}, limit=_MAX_ROWS, offset=0
    )
    return [agent_definition_out(row) for row in rows]


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    return needle in str(_g(row, "agent_name") or "").lower() or needle in str(
        _g(row, "agent_id") or ""
    ).lower()


class AgentDefinitionService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list(self, query: AgentDefinitionListQuery) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {"deleted": False}
        order_by = resolve_order_by(
            query.sort_by,
            query.sort_order,
            allowed_sort_fields=_ALLOWED_SORT_FIELDS,
            default_order_by=[("agent_name", False)],
        )
        search_query = (query.search or "").strip()
        if search_query:
            rows = await self._h.list_records(
                _TABLE, filters, limit=_MAX_ROWS, offset=0, order_by=order_by
            )
            items = [
                agent_definition_out(row)
                for row in rows
                if _matches_search(row, search_query)
            ]
            total = len(items)
            offset = (page - 1) * page_size
            page_items = items[offset:offset + page_size]
        else:
            offset = (page - 1) * page_size
            rows = await self._h.list_records(
                _TABLE, filters, limit=page_size, offset=offset, order_by=order_by
            )
            page_items = [agent_definition_out(row) for row in rows]
            total = await self._h.count_records(_TABLE, filters)
        return {
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def rename(self, agent_id: str, agent_name: str) -> dict[str, Any] | None:
        """改有效行的 Agent Name；不存在或已失效返回 ``None``，重名抛异常。"""
        row = await self._h.get(_TABLE, {"agent_id": agent_id})
        if row is None or bool(_g(row, "deleted", False)):
            return None
        name = agent_name.strip()
        key = name.casefold()
        active = await self._h.list_records(
            _TABLE, {"deleted": False}, limit=_MAX_ROWS, offset=0
        )
        for other in active:
            if str(_g(other, "agent_id") or "") == agent_id:
                continue
            if str(_g(other, "agent_name") or "").casefold() == key:
                raise AgentNameDuplicated(name)
        updated = await self._h.update(
            _TABLE,
            {"agent_id": agent_id, "deleted": False},
            {"agent_name": name, "updated_at": utc_now()},
        )
        return agent_definition_out(updated) if updated is not None else None
