"""Agent 模板（agent_definition）手动维护：列表、新增、改名、删除。

记录由管理员手动新增：选择 Agent 配置模板与运行时模板后，``agent_id`` 仍按
两者的组合稳定生成；名称可修改，组合不可修改。
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from sqlalchemy.exc import IntegrityError

from manager_server.core.user_console.agent_identity import try_build_agent_identity
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, utc_now
from manager_server.models.agent_definition_models import AGENT_DEFINITION_TABLE_DEF
from manager_server.models.template_models import (
    AGENT_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
)

_TABLE = AGENT_DEFINITION_TABLE_DEF.table_name
_CFG = AGENT_TEMPLATE_TABLE_DEF.table_name
_RT = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_MAX_ROWS = 100_000
_ALLOWED_SORT_FIELDS = frozenset(
    {"agent_name", "agent_id", "created_at", "updated_at"}
)
# 单进程内串行化写操作：名称唯一性靠应用层比对，需避免并发同时"先查后插"。
# 按事件循环取锁：生产只有一个循环，测试会在不同 loop 间复用同一模块。
_WRITE_LOCKS: weakref.WeakKeyDictionary[Any, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _write_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _WRITE_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _WRITE_LOCKS[loop] = lock
    return lock


class AgentNameDuplicated(Exception):
    """Agent Name 与其他行重名（大小写不敏感）。"""


class AgentDefinitionExists(Exception):
    """该 Agent 配置模板与运行时模板的组合已存在。"""


class TemplateUnavailable(Exception):
    """指定的 Agent 配置模板或运行时模板不存在，或未启用。"""


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def agent_definition_out(
    row: Any,
    *,
    config_template_name: str | None = None,
    runtime_template_name: str | None = None,
) -> dict[str, Any]:
    from manager_server.core.agent_definition.keycloak_client import client_status

    return {
        "keycloak_client": client_status(row),
        "agent_id": str(_g(row, "agent_id") or ""),
        "agent_name": str(_g(row, "agent_name") or ""),
        "agent_config_template_id": str(_g(row, "agent_config_template_id") or ""),
        "agent_config_template_name": config_template_name,
        "runtime_template_id": str(_g(row, "runtime_template_id") or ""),
        "runtime_template_name": runtime_template_name,
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    return needle in str(_g(row, "agent_name") or "").lower() or needle in str(
        _g(row, "agent_id") or ""
    ).lower()


async def _template_name_maps(
    handler: DBHandler,
) -> tuple[dict[str, str], dict[str, str]]:
    """按模板 id 实时取名称，列表不再使用库内快照。"""
    cfg_rows = await handler.list_records(_CFG, {}, limit=_MAX_ROWS, offset=0)
    rt_rows = await handler.list_records(_RT, {}, limit=_MAX_ROWS, offset=0)
    cfg_names = {
        str(_g(row, "template_id") or ""): str(_g(row, "template_name") or "")
        for row in cfg_rows
    }
    rt_names = {
        str(_g(row, "template_id") or ""): str(_g(row, "template_name") or "")
        for row in rt_rows
    }
    return cfg_names, rt_names


def _out_with_names(
    row: Any, cfg_names: dict[str, str], rt_names: dict[str, str]
) -> dict[str, Any]:
    cfg_id = str(_g(row, "agent_config_template_id") or "")
    rt_id = str(_g(row, "runtime_template_id") or "")
    return agent_definition_out(
        row,
        config_template_name=cfg_names.get(cfg_id) or None,
        runtime_template_name=rt_names.get(rt_id) or None,
    )


async def list_agent_definitions(handler: DBHandler) -> list[dict[str, Any]]:
    """取全部行，供用户面按组合匹配名称。"""
    rows = await handler.list_records(_TABLE, {}, limit=_MAX_ROWS, offset=0)
    cfg_names, rt_names = await _template_name_maps(handler)
    return [_out_with_names(row, cfg_names, rt_names) for row in rows]


async def count_agent_config_template_references(
    handler: DBHandler, template_id: str
) -> int:
    """统计引用指定 Agent 配置模板的 Agent 模板行数。"""
    tid = str(template_id or "").strip()
    if not tid:
        return 0
    return await handler.count_records(
        _TABLE, {"agent_config_template_id": tid}
    )


async def count_runtime_template_references(
    handler: DBHandler, template_id: str
) -> int:
    """统计引用指定运行时模板的 Agent 模板行数。"""
    tid = str(template_id or "").strip()
    if not tid:
        return 0
    return await handler.count_records(_TABLE, {"runtime_template_id": tid})


class AgentDefinitionService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list(self, query: Any) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        order_by = resolve_order_by(
            query.sort_by,
            query.sort_order,
            allowed_sort_fields=_ALLOWED_SORT_FIELDS,
            default_order_by=[("agent_name", False)],
        )
        cfg_names, rt_names = await _template_name_maps(self._h)
        search_query = (query.search or "").strip()
        if search_query:
            rows = await self._h.list_records(
                _TABLE, {}, limit=_MAX_ROWS, offset=0, order_by=order_by
            )
            items = [
                _out_with_names(row, cfg_names, rt_names)
                for row in rows
                if _matches_search(row, search_query)
            ]
            total = len(items)
            offset = (page - 1) * page_size
            page_items = items[offset:offset + page_size]
        else:
            offset = (page - 1) * page_size
            rows = await self._h.list_records(
                _TABLE, {}, limit=page_size, offset=offset, order_by=order_by
            )
            page_items = [
                _out_with_names(row, cfg_names, rt_names) for row in rows
            ]
            total = await self._h.count_records(_TABLE, {})
        return {
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def create(
        self,
        agent_config_template_id: str,
        runtime_template_id: str,
        agent_name: str,
    ) -> dict[str, Any]:
        cfg_id = str(agent_config_template_id or "").strip()
        rt_id = str(runtime_template_id or "").strip()
        name = str(agent_name or "").strip()
        identity = try_build_agent_identity(rt_id, cfg_id)
        if identity is None:
            raise TemplateUnavailable("template ids are invalid")
        agent_id = identity[0]
        cfg = await self._h.get(_CFG, {"template_id": cfg_id})
        if cfg is None or not bool(_g(cfg, "enabled", False)):
            raise TemplateUnavailable("agent config template is unavailable")
        rt = await self._h.get(_RT, {"template_id": rt_id})
        if rt is None or not bool(_g(rt, "enabled", False)):
            raise TemplateUnavailable("runtime template is unavailable")
        if not name:
            raise ValueError("agent_name must not be empty")

        async with _write_lock():
            existing = await self._h.get(_TABLE, {"agent_id": agent_id})
            if existing is not None:
                raise AgentDefinitionExists(agent_id)
            await self._assert_name_free(name)
            now = utc_now()
            try:
                row = await self._h.create(
                    _TABLE,
                    {
                        "agent_id": agent_id,
                        "agent_name": name,
                        "agent_config_template_id": cfg_id,
                        "runtime_template_id": rt_id,
                        "agent_config_template_name": None,
                        "runtime_template_name": None,
                        "deleted": False,
                        "deleted_reason": None,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            except IntegrityError:
                # 并发插入撞唯一索引：按组合重复处理，避免冒泡成 500。
                raise AgentDefinitionExists(agent_id) from None
        return agent_definition_out(
            row,
            config_template_name=str(_g(cfg, "template_name") or "") or None,
            runtime_template_name=str(_g(rt, "template_name") or "") or None,
        )

    async def rename(self, agent_id: str, agent_name: str) -> dict[str, Any] | None:
        """修改名称；行不存在返回 ``None``，重名抛异常。"""
        name = agent_name.strip()
        async with _write_lock():
            row = await self._h.get(_TABLE, {"agent_id": agent_id})
            if row is None:
                return None
            await self._assert_name_free(name, skip_agent_id=agent_id)
            updated = await self._h.update(
                _TABLE,
                {"agent_id": agent_id},
                {"agent_name": name, "updated_at": utc_now()},
            )
        if updated is None:
            return None
        cfg_names, rt_names = await _template_name_maps(self._h)
        return _out_with_names(updated, cfg_names, rt_names)

    async def delete(self, agent_id: str) -> bool:
        from manager_server.core.agent_definition.keycloak_client import AgentClientService

        return await AgentClientService(self._h).remove(agent_id, delete_agent=True)

    async def _assert_name_free(
        self, name: str, *, skip_agent_id: str | None = None
    ) -> None:
        key = name.casefold()
        rows = await self._h.list_records(_TABLE, {}, limit=_MAX_ROWS, offset=0)
        for other in rows:
            if skip_agent_id and str(_g(other, "agent_id") or "") == skip_agent_id:
                continue
            if str(_g(other, "agent_name") or "").casefold() == key:
                raise AgentNameDuplicated(name)
