"""Embedding 模板 embedding_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.push_template_to_gateway import (
    assert_template_deletable,
    count_agent_template_reference_counts,
    update_template_on_referencing_gateways,
    delete_template_on_referencing_gateways,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.template_models import EMBEDDING_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_schemas import (
    EmbeddingTemplateCreateBody,
    EmbeddingTemplateListQuery,
    EmbeddingTemplateOut,
    EmbeddingTemplateUpdateBody,
)
from manager_server.schemas.template_slot_schemas import EMBEDDING_MODEL_SLOT

_EMBEDDING_TEMPLATE_TABLE = EMBEDDING_TEMPLATE_TABLE_DEF.table_name
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "model_provider",
    "model_id",
    "api_base",
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
        str(getattr(row, "model_provider", "") or ""),
        str(getattr(row, "model_id", "") or ""),
        str(getattr(row, "api_base", "") or ""),
        *(getattr(row, "embed_tags", None) or []),
    ]
    return any(needle in str(field).lower() for field in fields)


def row_to_out(row: Any, *, reference_count: int = 0) -> EmbeddingTemplateOut:
    embed_tags = row.embed_tags
    if embed_tags is not None and not isinstance(embed_tags, list):
        embed_tags = list(embed_tags) if embed_tags else None
    return EmbeddingTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        embed_tags=embed_tags,
        api_base=row.api_base,
        api_key=row.api_key,
        model_id=row.model_id,
        model_provider=row.model_provider,
        parameters=row.parameters,
        client_config=row.client_config,
        enabled=row.enabled,
        reference_count=reference_count,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class EmbeddingTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _reference_counts(self) -> dict[str, int]:
        """被引用数：在 Agent 模板 embedding_model 槽位引用本类模板的 Agent 模板数。"""
        return await count_agent_template_reference_counts(
            self._handler, slot_keys=frozenset({EMBEDDING_MODEL_SLOT})
        )

    async def create(
        self,
        body: EmbeddingTemplateCreateBody,
    ) -> EmbeddingTemplateOut:
        now = utc_now()
        payload = {
            **body.model_dump(),
            "template_id": new_uuid4(),
            "created_at": now,
            "updated_at": now,
        }
        created = await self._handler.create(_EMBEDDING_TEMPLATE_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> EmbeddingTemplateOut | None:
        row = await self._handler.get(
            _EMBEDDING_TEMPLATE_TABLE, {"template_id": template_id}
        )
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

    async def list_templates(
        self,
        query: EmbeddingTemplateListQuery,
    ) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        provider_query = (query.model_provider or "").strip()
        if provider_query:
            filters["model_provider"] = provider_query
        reference_counts = await self._reference_counts()
        order_by = resolve_order_by(
            query.sort_by,
            query.sort_order,
            allowed_sort_fields=_ALLOWED_SORT_FIELDS,
        )
        search_query = (query.search or "").strip()
        if search_query:
            rows = await self._handler.list_records(
                _EMBEDDING_TEMPLATE_TABLE,
                filters,
                limit=10_000,
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
            total = len(items)
            offset = (page - 1) * page_size
            return {
                "items": items[offset:offset + page_size],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _EMBEDDING_TEMPLATE_TABLE,
            filters,
            limit=page_size,
            offset=offset,
            order_by=order_by,
        )
        total = await self._handler.count_records(
            _EMBEDDING_TEMPLATE_TABLE, filters
        )
        items = [
            row_to_out(
                row, reference_count=reference_counts.get(str(row.template_id), 0)
            ).model_dump(mode="json")
            for row in rows
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
        body: EmbeddingTemplateUpdateBody,
    ) -> EmbeddingTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)
        existing = await self._handler.get(
            _EMBEDDING_TEMPLATE_TABLE, {"template_id": template_id}
        )
        if existing is None:
            return None
        if not updates:
            counts = await self._reference_counts()
            return row_to_out(existing, reference_count=counts.get(str(existing.template_id), 0))
        await update_template_on_referencing_gateways(
            self._handler,
            "embedding_templates",
            template_id,
            updates,
        )
        row = await self._handler.update(
            _EMBEDDING_TEMPLATE_TABLE,
            {"template_id": template_id},
            {**updates, "updated_at": utc_now()},
        )
        if row is None:
            return None
        counts = await self._reference_counts()
        return row_to_out(row, reference_count=counts.get(str(row.template_id), 0))

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(
            _EMBEDDING_TEMPLATE_TABLE, {"template_id": template_id}
        )
        if row is None:
            return False
        await assert_template_deletable(
            self._handler, template_id, "embedding_templates"
        )
        await delete_template_on_referencing_gateways(
            self._handler,
            "embedding_templates",
            template_id,
        )
        return await self._handler.delete(
            _EMBEDDING_TEMPLATE_TABLE, {"template_id": template_id}
        )
