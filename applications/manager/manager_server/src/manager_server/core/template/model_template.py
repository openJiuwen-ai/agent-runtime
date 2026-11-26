"""模型模板 model_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.push_template_to_gateway import (
    assert_template_deletable,
    assert_template_not_referenced,
    count_agent_template_reference_counts,
    update_template_on_referencing_gateways,
    delete_template_on_referencing_gateways,
    resync_react_templates_referencing_model,
)
from manager_server.core.template.compressor_model_resolver import (
    count_react_model_reference_counts,
    count_react_model_references,
)
from manager_server.schemas.template_schemas import (
    ModelTemplateCreateBody,
    ModelTemplateListQuery,
    ModelTemplateOut,
    ModelTemplateUpdateBody,
)
from manager_server.schemas.template_slot_schemas import MODEL_TEMPLATE_SLOTS
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.template_models import MODEL_TEMPLATE_TABLE_DEF

_MODEL_TEMPLATE_TABLE = MODEL_TEMPLATE_TABLE_DEF.table_name
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "model_provider",
    "model_id",
    "model_type",
    "api_base",
    "updated_at",
})

# 压缩器内联配置（model/model_client）只依赖这些字段：仅当它们变更时才需要
# 重推引用本模型的 react 模板，避免改 template_name/description 等无关字段时额外全量重推。
_RESOLVER_RELEVANT_FIELDS = frozenset({
    "model_id",
    "model_provider",
    "api_key",
    "api_base",
    "timeout",
    "retry_count",
    "verify_ssl",
    "parameters",
    "data",
})


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(getattr(row, "template_id", "") or ""),
        str(getattr(row, "template_name", "") or ""),
        str(getattr(row, "description", "") or ""),
        str(getattr(row, "model_provider", "") or ""),
        str(getattr(row, "model_id", "") or ""),
        str(getattr(row, "api_base", "") or ""),
        *(getattr(row, "model_type", None) or []),
    ]
    return any(needle in field.lower() for field in fields)


def row_to_out(row: Any, *, reference_count: int = 0) -> ModelTemplateOut:
    return ModelTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        model_type=row.model_type,
        api_base=row.api_base,
        api_key=row.api_key,
        model_id=row.model_id,
        model_provider=row.model_provider,
        parameters=row.parameters,
        timeout=row.timeout,
        retry_count=row.retry_count,
        verify_ssl=row.verify_ssl,
        enabled=row.enabled,
        reference_count=reference_count,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ModelTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _reference_counts(self) -> dict[str, int]:
        """被引用数：Agent 模板各模型槽位引用数 + react 模板压缩器引用数（与删除保护同源）。"""
        counts = await count_agent_template_reference_counts(
            self._handler, slot_keys=MODEL_TEMPLATE_SLOTS
        )
        for model_id, num in (await count_react_model_reference_counts(self._handler)).items():
            counts[model_id] = counts.get(model_id, 0) + num
        return counts

    @staticmethod
    def _build_row_for_create(
        body: ModelTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "model_type": body.model_type,
            "api_base": body.api_base,
            "api_key": body.api_key,
            "model_id": body.model_id,
            "model_provider": body.model_provider,
            "parameters": body.parameters,
            "timeout": body.timeout,
            "retry_count": body.retry_count,
            "verify_ssl": body.verify_ssl,
            "enabled": body.enabled,
            "data": body.data,
        }

    async def create(
        self,
        body: ModelTemplateCreateBody,
    ) -> ModelTemplateOut:
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_MODEL_TEMPLATE_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> ModelTemplateOut | None:
        row = await self._handler.get(_MODEL_TEMPLATE_TABLE, {"template_id": template_id})
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

    async def list_templates(self, query: ModelTemplateListQuery) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        provider_query = (query.model_provider or "").strip()
        if provider_query:
            filters["model_provider"] = provider_query
        model_type = query.model_type

        reference_counts = await self._reference_counts()
        order_by = resolve_order_by(
            query.sort_by, query.sort_order, allowed_sort_fields=_ALLOWED_SORT_FIELDS
        )
        search_query = (query.search or "").strip()
        if search_query or model_type:
            rows = await self._handler.list_records(
                _MODEL_TEMPLATE_TABLE,
                filters,
                limit=10_000,
                offset=0,
                order_by=order_by,
            )
            items = []
            for row in rows:
                if model_type and model_type not in (getattr(row, "model_type", None) or []):
                    continue
                if search_query and not _matches_search(row, search_query):
                    continue
                items.append(
                    row_to_out(
                        row, reference_count=reference_counts.get(str(row.template_id), 0)
                    ).model_dump(mode="json")
                )
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
            _MODEL_TEMPLATE_TABLE,
            filters,
            limit=page_size,
            offset=offset,
            order_by=order_by,
        )
        total = await self._handler.count_records(_MODEL_TEMPLATE_TABLE, filters)
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
        body: ModelTemplateUpdateBody,
    ) -> ModelTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(
                _MODEL_TEMPLATE_TABLE, {"template_id": template_id}
            )
            if row is None:
                return None
            counts = await self._reference_counts()
            return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

        existing = await self._handler.get(
            _MODEL_TEMPLATE_TABLE, {"template_id": template_id}
        )
        if existing is None:
            return None

        if updates.get("enabled") is False and bool(getattr(existing, "enabled", True)):
            await assert_template_not_referenced(
                self._handler, template_id, "model_templates", action="disable"
            )

        await update_template_on_referencing_gateways(
            self._handler,
            "model_templates",
            template_id,
            updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        row = await self._handler.update(
            _MODEL_TEMPLATE_TABLE, {"template_id": template_id}, payload
        )
        if row is None:
            return None
        # 模型模板变更已落库后再重推引用它的 react 模板，
        # 确保 resolver 读到的是最新模型配置（避免网关内联过期配置）。
        # 仅当模型解析相关字段变化才重推；resync 内部已 try/except，失败只记日志不阻断更新。
        if _RESOLVER_RELEVANT_FIELDS & updates.keys():
            await resync_react_templates_referencing_model(self._handler, template_id)
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(
            _MODEL_TEMPLATE_TABLE, {"template_id": template_id}
        )
        if row is None:
            return False
        react_refs = await count_react_model_references(self._handler, template_id)
        if react_refs > 0:
            raise ValueError(
                f"cannot delete model template: referenced by {react_refs} react template(s)"
            )
        await assert_template_deletable(
            self._handler, template_id, "model_templates"
        )
        await delete_template_on_referencing_gateways(
            self._handler,
            "model_templates",
            template_id,
        )
        return await self._handler.delete(
            _MODEL_TEMPLATE_TABLE, {"template_id": template_id}
        )
