"""服务配置模板 service_config_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.service_config_container import (
    extract_wire_containers,
    hydrate_data_with_containers,
    main_image_from_table,
    strip_containers_from_data,
    upsert_wire_containers,
)
from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.models.instance_resource_models import (
    INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
)
from manager_server.models.template_models import SERVICE_CONFIG_TEMPLATE_TABLE_DEF
from manager_server.schemas.template_schemas import (
    ServiceConfigTemplateCreateBody,
    ServiceConfigTemplateListQuery,
    ServiceConfigTemplateOut,
    ServiceConfigTemplateUpdateBody,
)

_TABLE = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_SERVICE_RESOURCE_TABLE = INSTANCE_SERVICE_RESOURCE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "template_name",
    "description",
    "updated_at",
})


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


async def _assert_service_config_deletable(
    handler: DBHandler,
    template_id: str,
) -> None:
    """删除前校验：仍被实例服务资源引用则拒绝。"""
    tid = str(template_id or "").strip()
    if not tid:
        return
    grant_count = await handler.count_records(
        _SERVICE_RESOURCE_TABLE,
        {"ref_template_id": tid},
    )
    if grant_count > 0:
        raise ValueError(
            f"cannot delete template: {grant_count} instance service "
            "resource reference(s) exist, remove grants first"
        )


def _matches_search(row: Any, query: str, *, main_image: str | None = None) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(_g(row, "template_id", "") or ""),
        str(_g(row, "template_name", "") or ""),
        str(_g(row, "description", "") or ""),
        str(main_image or ""),
    ]
    return any(needle in field.lower() for field in fields)


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


async def row_to_out(handler: DBHandler, row: Any) -> ServiceConfigTemplateOut:
    sidecar_ids = _g(row, "sidecar_container_ids")
    if sidecar_ids is not None and not isinstance(sidecar_ids, list):
        sidecar_ids = None
    data = await hydrate_data_with_containers(handler, row)
    return ServiceConfigTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        namespace=str(_g(row, "namespace", "default") or "default"),
        node_name=_g(row, "node_name"),
        fs_group=_g(row, "fs_group"),
        pod_name=str(_g(row, "pod_name", "agentserver") or "agentserver"),
        sse_path=str(_g(row, "sse_path", "/sse") or "/sse"),
        kubeconfig=_g(row, "kubeconfig"),
        ready_timeout=_as_int(_g(row, "ready_timeout"), 300),
        ready_poll_interval=_as_int(_g(row, "ready_poll_interval"), 2),
        main_container_id=_g(row, "main_container_id"),
        sidecar_container_ids=sidecar_ids,
        volumes=_g(row, "volumes") if isinstance(_g(row, "volumes"), list) else None,
        main_image=await main_image_from_table(handler, row),
        min_idle_pods=_as_int(_g(row, "min_idle_pods"), 0),
        pod_concurrency=_as_int(_g(row, "pod_concurrency"), 2),
        pod_ttl=_as_int(_g(row, "pod_ttl"), 300),
        message_timeout=_as_int(_g(row, "message_timeout"), 600),
        scope_concurrency=_as_int(_g(row, "scope_concurrency"), 3),
        session_ttl=_as_int(_g(row, "session_ttl"), 60),
        enabled=bool(_g(row, "enabled", True)),
        data=data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


def _split_body_data(data: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    wires = extract_wire_containers(data)
    return wires, strip_containers_from_data(data)


class ServiceConfigTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    @staticmethod
    def _build_row_for_create(
        body: ServiceConfigTemplateCreateBody, *, template_id: str, data: dict[str, Any] | None
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "namespace": body.namespace or "default",
            "node_name": body.node_name,
            "fs_group": body.fs_group,
            "pod_name": body.pod_name or "agentserver",
            "sse_path": body.sse_path or "/sse",
            "kubeconfig": body.kubeconfig,
            "ready_timeout": body.ready_timeout,
            "ready_poll_interval": body.ready_poll_interval,
            "main_container_id": body.main_container_id,
            "sidecar_container_ids": body.sidecar_container_ids,
            "volumes": body.volumes,
            "min_idle_pods": body.min_idle_pods,
            "pod_concurrency": body.pod_concurrency,
            "pod_ttl": body.pod_ttl,
            "message_timeout": body.message_timeout,
            "scope_concurrency": body.scope_concurrency,
            "session_ttl": body.session_ttl,
            "enabled": body.enabled,
            "data": data,
        }

    async def create(
        self,
        body: ServiceConfigTemplateCreateBody,
    ) -> ServiceConfigTemplateOut:
        wires, data = _split_body_data(body.data)
        if wires:
            await upsert_wire_containers(self._handler, wires)
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid, data=data)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_TABLE, payload)
        return await row_to_out(self._handler, created)

    async def get(self, template_id: str) -> ServiceConfigTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        return await row_to_out(self._handler, row)

    async def list_templates(
        self,
        query: ServiceConfigTemplateListQuery,
    ) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {}
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        if query.namespace is not None:
            filters["namespace"] = query.namespace

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
            items: list[dict[str, Any]] = []
            for r in rows:
                main_image = await main_image_from_table(self._handler, r)
                if not _matches_search(r, search_query, main_image=main_image):
                    continue
                items.append((await row_to_out(self._handler, r)).model_dump(mode="json"))
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
            (await row_to_out(self._handler, r)).model_dump(mode="json") for r in rows
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
        body: ServiceConfigTemplateUpdateBody,
    ) -> ServiceConfigTemplateOut | None:
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(_TABLE, {"template_id": template_id})
            return await row_to_out(self._handler, row) if row is not None else None

        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None

        if "data" in updates:
            wires, data = _split_body_data(updates.get("data"))
            if wires:
                await upsert_wire_containers(self._handler, wires)
            updates["data"] = data

        payload = dict(updates)
        payload["updated_at"] = utc_now()
        row = await self._handler.update(
            _TABLE, {"template_id": template_id}, payload
        )
        if row is None:
            return None

        # Runtime config_sync 从 MDB 读模板，须先落库再按引用重推。
        from manager_server.core.template.push_template_to_runtime import (
            update_service_template_on_referencing_runtimes,
        )

        await update_service_template_on_referencing_runtimes(
            self._handler, template_id
        )
        return await row_to_out(self._handler, row)

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return False
        await _assert_service_config_deletable(self._handler, template_id)
        # 容器表为全局目录，删模板不级联删 container 行（可能被其它模板引用）。
        return await self._handler.delete(_TABLE, {"template_id": template_id})
