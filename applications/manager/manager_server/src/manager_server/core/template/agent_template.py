"""Agent 模板 agent_template 业务逻辑（平台全局目录）。"""

from __future__ import annotations

from asyncio import Lock, timeout
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import time
from typing import Any, cast
from weakref import WeakValueDictionary

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from sqlalchemy import delete, select, update

from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.template_ref import read_template_ref_from_row
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, strip_optional, utc_now
from manager_server.integrations.keycloak.agent_clients import (
    RegistrationConflict,
    RegistrationError,
)
from manager_server.models.instance_resource_models import INSTANCE_AGENT_RESOURCE_TABLE_DEF
from manager_server.models.template_models import AGENT_TEMPLATE_TABLE_DEF
from manager_server.models.template_models import A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_slot_schemas import A2A_ACCESS_POLICY_SLOT
from manager_server.schemas.template_schemas import (
    AGENT_TEMPLATE_OPERATION_KEY,
    AgentTemplateCreateBody,
    AgentTemplateListQuery,
    AgentTemplateUpdateBody,
    MANAGED_AGENT_CLIENT_KEY,
)

_log = get_logger(__name__)
_AGENT_TPL = AGENT_TEMPLATE_TABLE_DEF.table_name
_A2A_POLICY_TPL = A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF.table_name
_template_mutation_locks: WeakValueDictionary[str, Lock] = WeakValueDictionary()
_MUTATION_TIMEOUT = 120
# The lease outlives the network operation.
_MUTATION_LEASE = 300
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
    fields = [
        str(_g(row, "template_id", "") or ""),
        str(_g(row, "template_name", "") or ""),
        str(_g(row, "description", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def _operation(data: dict | None) -> dict:
    marker = (data or {}).get(AGENT_TEMPLATE_OPERATION_KEY)
    return marker if isinstance(marker, dict) else {}


def _template_data(data: dict | None) -> dict | None:
    if data is None or AGENT_TEMPLATE_OPERATION_KEY not in data:
        return data
    remaining = {key: value for key, value in data.items() if key != AGENT_TEMPLATE_OPERATION_KEY}
    return None if not remaining and _operation(data).get("was_null") else remaining


def public_agent_template_data(data: dict | None) -> dict | None:
    data = _template_data(data)
    if data is None or MANAGED_AGENT_CLIENT_KEY not in data:
        return data
    return {key: value for key, value in data.items() if key != MANAGED_AGENT_CLIENT_KEY} or None


class TemplateMutationBusy(RegistrationConflict):
    def __init__(self):
        super().__init__("This template is being changed. Retry shortly.", code="template_busy")


@dataclass
class _TemplateMutation:
    row: Any
    data: dict | None
    operation_id: str
    completed: bool = False


async def _assert_no_agent_definitions(
    handler: DBHandler, *, config_template_id: str, action: str
) -> None:
    """模板被 Agent 模板引用时拒绝删除/停用，避免出现悬空组合。"""
    from manager_server.core.agent_definition.service import (
        count_agent_config_template_references,
    )
    from manager_server.core.template.errors import TemplateInUseError

    ref_count = await count_agent_config_template_references(
        handler, config_template_id
    )
    if ref_count > 0:
        raise TemplateInUseError(
            "agent_definitions_referenced",
            ref_count,
            f"cannot {action} template: {ref_count} agent template(s) reference it, "
            "delete those agent templates first",
        )


def agent_template_out(row: Any, *, reference_count: int = 0) -> dict[str, Any]:
    data = _g(row, "data")
    result = {
        "id": _g(row, "id"),
        "template_id": _g(row, "template_id"),
        "template_name": _g(row, "template_name"),
        "description": _g(row, "description"),
        "template_ref": read_template_ref_from_row(row),
        "enabled": bool(_g(row, "enabled", True)),
        "data": public_agent_template_data(data),
        "reference_count": reference_count,
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }

    return result


def row_to_sync(row: Any) -> dict[str, Any]:
    payload = agent_template_out(row)
    for key in ("id", "created_at", "updated_at", "reference_count"):
        payload.pop(key, None)
    return payload


async def _reference_counts(handler: DBHandler) -> dict[str, int]:
    """批量取各 Agent 模板的 Agent 引用数（局部导入避免循环依赖）。"""
    from manager_server.core.template.push_template_to_gateway import (
        count_agent_reference_counts,
    )

    return await count_agent_reference_counts(handler)


async def _reference_count(handler: DBHandler, template_id: str) -> int:
    """取单个 Agent 模板的 Agent 引用数（局部导入避免循环依赖）。"""
    from manager_server.core.template.push_template_to_gateway import (
        count_agent_references_for_template,
    )

    return await count_agent_references_for_template(handler, template_id)


class AgentTemplateService:
    """Agent 模板目录 CRUD。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def _validate_a2a_policy_ref(self, template_ref: dict[str, list[str]]) -> None:
        refs = template_ref.get(A2A_ACCESS_POLICY_SLOT, [])
        if not refs:
            return
        policy_id = refs[0]
        if "${" in policy_id or " or " in policy_id.lower():
            raise ValueError("a2a_access_policy must directly reference one policy_id")
        if await self._h.get(_A2A_POLICY_TPL, {"policy_id": policy_id}) is None:
            raise ValueError(f"unknown a2a access policy id: {policy_id}")

    async def list(self, query: AgentTemplateListQuery) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled

        order_by = resolve_order_by(
            query.sort_by, query.sort_order, allowed_sort_fields=_ALLOWED_SORT_FIELDS
        )
        reference_counts = await _reference_counts(self._h)
        search_query = (query.search or "").strip()
        if search_query:
            rows = await self._h.list_records(
                _AGENT_TPL, filters, limit=10_000, offset=0, order_by=order_by
            )
            items = [
                agent_template_out(
                    row,
                    reference_count=reference_counts.get(str(row.template_id), 0),
                )
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
            "items": [
                agent_template_out(
                    r,
                    reference_count=reference_counts.get(str(r.template_id), 0),
                )
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get(self, template_id: str) -> dict[str, Any] | None:
        row = await self._h.get(_AGENT_TPL, {"template_id": template_id})
        if row is None:
            return None
        return agent_template_out(
            row,
            reference_count=await _reference_count(self._h, template_id),
        )

    async def create(self, body: AgentTemplateCreateBody) -> dict[str, Any]:
        await self._validate_a2a_policy_ref(body.template_ref)
        template_id = new_uuid4()
        now = utc_now()
        await self._h.create(
            _AGENT_TPL,
            {
                "template_id": template_id,
                "template_name": body.template_name,
                "description": strip_optional(body.description),
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

    @asynccontextmanager
    async def _lock_template(self, template_id: str):
        handler = cast(SQLAlchemyHandler, self._h)
        table = handler.get_table(_AGENT_TPL)
        match = table.c.template_id == template_id
        async with handler.session_factory.begin() as session:
            if handler.engine.dialect.name == "sqlite":
                # SQLite has no SELECT FOR UPDATE. Obtain its write lock for this short transaction.
                await session.execute(update(table).where(match).values(updated_at=table.c.updated_at))
            result = await session.execute(select(table).where(match).with_for_update())
            yield session, table, result.one_or_none()

    @asynccontextmanager
    async def reserve_template(self, template_id: str):
        """Reserve a template for configuration updates or Agent client dispatch."""
        # Keep local callers ordered, and reserve the row across Manager processes.
        lock = _template_mutation_locks.setdefault(template_id, Lock())
        if lock.locked():
            raise TemplateMutationBusy()
        async with lock:
            async with self._lock_template(template_id) as (session, table, row):
                operation = None
                if row is not None:
                    if _operation(row.data).get("expires_at", 0) > time():
                        raise TemplateMutationBusy()
                    data = _template_data(row.data)
                    operation = _TemplateMutation(row, data, new_uuid4())
                    marker = {
                        "id": operation.operation_id, "expires_at": time() + _MUTATION_LEASE,
                        "was_null": data is None,
                    }
                    await session.execute(update(table).where(table.c.id == row.id).values(
                        data={**(data or {}), AGENT_TEMPLATE_OPERATION_KEY: marker},
                    ))
            try:
                async with timeout(_MUTATION_TIMEOUT):
                    yield operation
            except TimeoutError:
                raise RegistrationError("Agent template operation timed out. Please retry.") from None
            finally:
                if operation is not None and not operation.completed:
                    try:
                        await self._finish_template(operation)
                    except Exception as exc:
                        _log.warning("Agent template reservation release failed", template_id=template_id,
                                     error_type=type(exc).__name__)

    async def _finish_template(
        self, operation: _TemplateMutation, *, changes: dict[str, Any] | None = None,
        deleting: bool = False,
    ) -> None:
        template_id = operation.row.template_id
        async with self._lock_template(template_id) as (session, table, row):
            if row is None or _operation(row.data).get("id") != operation.operation_id:
                raise TemplateMutationBusy()
            if deleting:
                handler = cast(SQLAlchemyHandler, self._h)
                grants = handler.get_table(INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name)
                await session.execute(delete(grants).where(grants.c.ref_template_id == template_id))
                await session.execute(delete(table).where(table.c.id == row.id))
            else:
                values = {"data": operation.data, **(changes or {})}
                if changes:
                    values["updated_at"] = utc_now()
                await session.execute(update(table).where(table.c.id == row.id).values(**values))
        operation.completed = True

    async def update(self, template_id: str, body: AgentTemplateUpdateBody) -> dict[str, Any] | None:
        from manager_server.core.template.push_template_to_gateway import (
            AGENT_TEMPLATES_KIND,
            update_template_on_referencing_gateways,
        )

        if body.template_ref is not None:
            await self._validate_a2a_policy_ref(body.template_ref)
        async with self.reserve_template(template_id) as operation:
            if operation is None:
                return None
            updates = body.model_dump(exclude_unset=True)
            if "description" in updates:
                updates["description"] = strip_optional(body.description)
            # exclude_unset 会保留显式 null；这些列不允许被清空，剔除以免写库/校验踩坑
            for key in ("enabled", "template_name", "template_ref"):
                if updates.get(key) is None:
                    updates.pop(key, None)
            if not updates:
                return agent_template_out(
                    operation.row,
                    reference_count=await _reference_count(self._h, template_id),
                )
            if updates.get("enabled") is False and bool(
                _g(operation.row, "enabled", True)
            ):
                await _assert_no_agent_definitions(
                    self._h, config_template_id=template_id, action="disable"
                )

            await update_template_on_referencing_gateways(self._h, AGENT_TEMPLATES_KIND, template_id, updates)
            await self._finish_template(operation, changes=updates)
        return await self.get(template_id)

    async def delete(self, template_id: str) -> bool:
        """删除 Agent 模板：仍被实例上的 Agent 或 Agent 模板引用时拒绝删除。"""
        from manager_server.core.template.push_template_to_gateway import (
            assert_agent_template_deletable,
        )

        async with self.reserve_template(template_id) as operation:
            if operation is None:
                return False
            # 在预留锁内校验，尽量缩小与“新增 Agent 授权”的竞态窗口
            await assert_agent_template_deletable(self._h, template_id)
            await _assert_no_agent_definitions(
                self._h, config_template_id=template_id, action="delete"
            )

            await self._finish_template(operation, deleting=True)
        _log.info("[Template] agent_template.delete", template_id=template_id)
        return True

    async def exists(self, template_id: str) -> bool:
        return await self._h.get(_AGENT_TPL, {"template_id": template_id}) is not None

    async def get_row(self, template_id: str) -> Any | None:
        return await self._h.get(_AGENT_TPL, {"template_id": template_id})
