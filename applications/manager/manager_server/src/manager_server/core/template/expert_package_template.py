"""专家包模板 expert_package_template 业务逻辑。"""

from __future__ import annotations

import json
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.application_config.hub_package_catalog import HIDDEN_HUB_ASSET_IDS
from manager_server.core.template.push_template_to_gateway import (
    assert_template_deletable,
    count_agent_template_reference_counts,
    delete_template_on_referencing_gateways,
    update_template_on_referencing_gateways,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.template_models import EXPERT_PACKAGE_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_schemas import (
    ExpertPackageItem,
    ExpertPackageTemplateCreateBody,
    ExpertPackageTemplateListQuery,
    ExpertPackageTemplateOut,
    ExpertPackageTemplateUpdateBody,
)
from manager_server.schemas.template_slot_schemas import EXPERT_PACKAGE_SLOT

_TABLE = EXPERT_PACKAGE_TEMPLATE_TABLE_DEF.table_name
_KIND = "expert_package_templates"
_LIST_ALL_CAP = 10_000
_PACKAGE_KINDS = frozenset({"agent_template", "agent_group"})
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "updated_at",
})


def assert_selectable_hub_asset(asset_id: str) -> str:
    """Hub 标识必须能出现在新建页清单里。隐藏的四个包不能写入组合。"""
    asset = str(asset_id or "").strip()
    if not asset or len(asset) > 128 or asset in {".", ".."}:
        raise ValueError("invalid hub asset id")
    if "/" in asset or "\\" in asset:
        raise ValueError("invalid hub asset id")
    if asset in HIDDEN_HUB_ASSET_IDS:
        raise ValueError("hidden hub asset cannot be included in an expert package")
    return asset


def normalize_package_items(items: list[Any]) -> list[dict[str, str]]:
    """按 (kind, asset_id) 去重，并拒绝隐藏或非法的 Hub 标识。"""
    seen: set[tuple[str, str]] = set()
    normalized: list[dict[str, str]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            raise ValueError("packages item must be an object")
        kind = str(item.get("kind") or "").strip()
        if kind not in _PACKAGE_KINDS:
            raise ValueError("package kind must be agent_template or agent_group")
        asset_id = assert_selectable_hub_asset(str(item.get("asset_id") or ""))
        version = str(item.get("version") or "").strip()
        if len(version) > 64:
            raise ValueError("version must be at most 64 characters")
        key = (kind, asset_id)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"kind": kind, "asset_id": asset_id, "version": version})
    if not normalized:
        raise ValueError("packages must contain at least one hub package")
    return normalized


def packages_from_row(row: Any) -> list[dict[str, str]]:
    raw = row.get("packages") if isinstance(row, dict) else getattr(row, "packages", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    packages: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        asset_id = str(item.get("asset_id") or "").strip()
        if kind not in _PACKAGE_KINDS or not asset_id:
            continue
        packages.append({
            "kind": kind,
            "asset_id": asset_id,
            "version": str(item.get("version") or ""),
        })
    return packages


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(getattr(row, "template_id", "") or ""),
        str(getattr(row, "template_name", "") or ""),
        str(getattr(row, "description", "") or ""),
        *(item["asset_id"] for item in packages_from_row(row)),
    ]
    return any(needle in field.lower() for field in fields)


def row_to_out(row: Any, *, reference_count: int = 0) -> ExpertPackageTemplateOut:
    return ExpertPackageTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        packages=[ExpertPackageItem(**item) for item in packages_from_row(row)],
        enabled=row.enabled,
        reference_count=reference_count,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ExpertPackageTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _reference_counts(self) -> dict[str, int]:
        """被引用数：Agent 模板 expert_package 槽里引用本行的 Agent 模板数。"""
        return await count_agent_template_reference_counts(
            self._handler,
            slot_keys=frozenset({EXPERT_PACKAGE_SLOT}),
        )

    async def create(self, body: ExpertPackageTemplateCreateBody) -> ExpertPackageTemplateOut:
        packages = normalize_package_items(body.packages)
        now = utc_now()
        created = await self._handler.create(
            _TABLE,
            {
                "template_id": new_uuid4(),
                "template_name": body.template_name,
                "description": body.description,
                "packages": packages,
                "enabled": body.enabled,
                "created_at": now,
                "updated_at": now,
            },
        )
        return row_to_out(created)

    async def get(self, template_id: str) -> ExpertPackageTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

    async def list_templates(self, query: ExpertPackageTemplateListQuery) -> dict[str, Any]:
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
                    row, reference_count=reference_counts.get(str(row.template_id), 0)
                ).model_dump(mode="json")
                for row in rows
                if _matches_search(row, search_query)
            ]
            offset = (page - 1) * page_size
            return {
                "items": items[offset:offset + page_size],
                "total": len(items),
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
        return {
            "items": [
                row_to_out(
                    row, reference_count=reference_counts.get(str(row.template_id), 0)
                ).model_dump(mode="json")
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update(
        self,
        template_id: str,
        body: ExpertPackageTemplateUpdateBody,
    ) -> ExpertPackageTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)
        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None
        if "packages" in updates:
            updates["packages"] = normalize_package_items(updates["packages"])
        if not updates:
            counts = await self._reference_counts()
            return row_to_out(existing, reference_count=counts.get(str(existing.template_id), 0))

        await update_template_on_referencing_gateways(
            self._handler,
            _KIND,
            template_id,
            updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        row = await self._handler.update(_TABLE, {"template_id": template_id}, payload)
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return False
        await assert_template_deletable(self._handler, template_id, _KIND)
        await delete_template_on_referencing_gateways(self._handler, _KIND, template_id)
        return await self._handler.delete(_TABLE, {"template_id": template_id})
