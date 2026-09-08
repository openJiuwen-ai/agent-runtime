"""Manager-owned outbound A2A access policy CRUD."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.push_template_to_gateway import (
    delete_template_on_referencing_gateways,
    reconcile_a2a_projection_on_referencing_gateways,
    update_template_on_referencing_gateways,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.template_ref import read_template_ref_from_row
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.template_models import (
    A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
    A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
)
from manager_server.schemas.template_schemas import (
    A2AAccessPolicyTemplateCreateBody,
    A2AAccessPolicyTemplateListQuery,
    A2AAccessPolicyTemplateOut,
    A2AAccessPolicyTemplateUpdateBody,
)

_TABLE = A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF.table_name
_AGENT_TABLE = A2A_OUTBOUND_TEMPLATE_TABLE_DEF.table_name
_AGENT_TEMPLATE_TABLE = "agent_template"
_LIST_ALL_CAP = 10_000
_REFERENCE_SCAN_PAGE_SIZE = 500
_ALLOWED_SORT_FIELDS = frozenset({"policy_name", "mode", "enabled", "updated_at"})


def row_to_out(row: Any, *, reference_count: int = 0) -> A2AAccessPolicyTemplateOut:
    members = row.member_template_ids
    if isinstance(members, str):
        try:
            members = json.loads(members)
        except (TypeError, ValueError):
            members = []
    if not isinstance(members, list):
        members = []
    members = [str(item) for item in members if item]
    return A2AAccessPolicyTemplateOut(
        id=row.id,
        policy_id=str(row.policy_id),
        policy_name=row.policy_name,
        description=row.description,
        mode=row.mode,
        member_template_ids=members,
        enabled=row.enabled,
        revision=row.revision,
        reference_count=reference_count,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    return any(
        needle in str(value or "").lower()
        for value in (row.policy_id, row.policy_name, row.description, row.mode)
    )


def row_to_sync(row: Any) -> dict[str, Any]:
    payload = row_to_out(row).model_dump(mode="json")
    for key in ("id", "created_at", "reference_count"):
        payload.pop(key, None)
    return payload


class A2AAccessPolicyTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _validate_members(self, member_template_ids: list[str]) -> None:
        missing = []
        for template_id in member_template_ids:
            if await self._handler.get(_AGENT_TABLE, {"template_id": template_id}) is None:
                missing.append(template_id)
        if missing:
            raise ValueError("unknown a2a outbound template ids: " + ", ".join(missing))

    async def _reference_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        offset = 0
        while True:
            rows = await self._handler.list_records(
                _AGENT_TEMPLATE_TABLE,
                {},
                limit=_REFERENCE_SCAN_PAGE_SIZE,
                offset=offset,
                order_by=[("id", False)],
            )
            for row in rows:
                for policy_id in read_template_ref_from_row(row).get("a2a_access_policy", []):
                    counts[policy_id] = counts.get(policy_id, 0) + 1
            if len(rows) < _REFERENCE_SCAN_PAGE_SIZE:
                return counts
            offset += len(rows)

    async def create(self, body: A2AAccessPolicyTemplateCreateBody) -> A2AAccessPolicyTemplateOut:
        await self._validate_members(body.member_template_ids)
        now = utc_now()
        values = body.model_dump()
        values.update(
            {"policy_id": new_uuid4(), "revision": 1, "created_at": now, "updated_at": now}
        )
        return row_to_out(await self._handler.create(_TABLE, values))

    async def get(self, policy_id: str) -> A2AAccessPolicyTemplateOut | None:
        row = await self._handler.get(_TABLE, {"policy_id": policy_id})
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(policy_id, 0))

    async def list_templates(self, query: A2AAccessPolicyTemplateListQuery) -> dict[str, Any]:
        reference_counts = await self._reference_counts()
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        if query.mode is not None:
            filters["mode"] = query.mode
        order_by = resolve_order_by(
            query.sort_by, query.sort_order, allowed_sort_fields=_ALLOWED_SORT_FIELDS
        )
        search = (query.search or "").strip()
        if search:
            rows = await self._handler.list_records(
                _TABLE, filters, limit=_LIST_ALL_CAP, offset=0, order_by=order_by
            )
            items = [
                row_to_out(
                    row, reference_count=reference_counts.get(str(row.policy_id), 0)
                ).model_dump(mode="json")
                for row in rows
                if _matches_search(row, search)
            ]
            total = len(items)
            offset = (query.page - 1) * query.page_size
            items = items[offset : offset + query.page_size]
        else:
            offset = (query.page - 1) * query.page_size
            rows = await self._handler.list_records(
                _TABLE, filters, limit=query.page_size, offset=offset, order_by=order_by
            )
            items = [
                row_to_out(
                    row, reference_count=reference_counts.get(str(row.policy_id), 0)
                ).model_dump(mode="json")
                for row in rows
            ]
            total = await self._handler.count_records(_TABLE, filters)
        return {"items": items, "total": total, "page": query.page, "page_size": query.page_size}

    async def update(
        self, policy_id: str, body: A2AAccessPolicyTemplateUpdateBody
    ) -> A2AAccessPolicyTemplateOut | None:
        existing = await self._handler.get(_TABLE, {"policy_id": policy_id})
        if existing is None:
            return None
        updates = body.model_dump(exclude_unset=True)
        if "member_template_ids" in updates:
            await self._validate_members(updates["member_template_ids"])
        if not updates:
            counts = await self._reference_counts()
            return row_to_out(existing, reference_count=counts.get(policy_id, 0))
        updates["revision"] = existing.revision + 1
        updates["updated_at"] = utc_now()
        row = await self._handler.update(_TABLE, {"policy_id": policy_id}, updates)
        if row is None:
            return None
        sync_payload = row_to_sync(row)
        sync_payload.pop("policy_id", None)
        await update_template_on_referencing_gateways(
            self._handler, "a2a_access_policies", policy_id, sync_payload
        )
        counts = await self._reference_counts()
        await reconcile_a2a_projection_on_referencing_gateways(self._handler, policy_id)
        return row_to_out(row, reference_count=counts.get(policy_id, 0))

    async def delete(self, policy_id: str) -> bool:
        existing = await self._handler.get(_TABLE, {"policy_id": policy_id})
        if existing is None:
            return False
        offset = 0
        while True:
            agent_templates = await self._handler.list_records(
                _AGENT_TEMPLATE_TABLE,
                {},
                limit=_REFERENCE_SCAN_PAGE_SIZE,
                offset=offset,
                order_by=[("id", False)],
            )
            for row in agent_templates:
                refs = read_template_ref_from_row(row).get("a2a_access_policy", [])
                if policy_id in refs:
                    raise ValueError("a2a access policy is referenced by an agent template")
            if len(agent_templates) < _REFERENCE_SCAN_PAGE_SIZE:
                break
            offset += len(agent_templates)
        await delete_template_on_referencing_gateways(
            self._handler, "a2a_access_policies", policy_id
        )
        return await self._handler.delete(_TABLE, {"policy_id": policy_id})
