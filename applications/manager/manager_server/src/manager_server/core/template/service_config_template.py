"""服务配置模板 service_config_template 业务逻辑。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

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
    "agent_image",
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



def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(_g(row, "template_id", "") or ""),
        str(_g(row, "template_name", "") or ""),
        str(_g(row, "description", "") or ""),
        str(_g(row, "agent_image", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def row_to_out(row: Any) -> ServiceConfigTemplateOut:
    sidecar_ids = _g(row, "sidecar_container_ids")
    if sidecar_ids is not None and not isinstance(sidecar_ids, list):
        sidecar_ids = None
    return ServiceConfigTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        agent_image=str(_g(row, "agent_image", "") or ""),
        namespace=str(_g(row, "namespace", "default") or "default"),
        node_name=_g(row, "node_name"),
        run_as_user=_g(row, "run_as_user"),
        run_as_group=_g(row, "run_as_group"),
        pod_name=str(_g(row, "pod_name", "agentserver") or "agentserver"),
        container_name=str(_g(row, "container_name", "agent") or "agent"),
        container_port=_as_int(_g(row, "container_port"), 8080),
        port_name=str(_g(row, "port_name", "http") or "http"),
        sse_port=_as_int(_g(row, "sse_port"), 8080),
        sse_path=str(_g(row, "sse_path", "/sse") or "/sse"),
        health_path=str(_g(row, "health_path", "/health") or "/health"),
        agent_env=_g(row, "agent_env") if isinstance(_g(row, "agent_env"), dict) else None,
        image_pull_policy=str(
            _g(row, "image_pull_policy", "IfNotPresent") or "IfNotPresent"
        ),
        kubeconfig=_g(row, "kubeconfig"),
        readiness_initial_delay=_as_int(_g(row, "readiness_initial_delay"), 5),
        readiness_period=_as_int(_g(row, "readiness_period"), 5),
        ready_timeout=_as_int(_g(row, "ready_timeout"), 300),
        ready_poll_interval=_as_int(_g(row, "ready_poll_interval"), 2),
        nfs_server=_g(row, "nfs_server"),
        nfs_path=_g(row, "nfs_path"),
        nfs_mount_path=_g(row, "nfs_mount_path"),
        agent_cpu_request=_g(row, "agent_cpu_request"),
        agent_memory_request=_g(row, "agent_memory_request"),
        agent_cpu_limit=_g(row, "agent_cpu_limit"),
        agent_memory_limit=_g(row, "agent_memory_limit"),
        sidecars=_g(row, "sidecars") if isinstance(_g(row, "sidecars"), list) else None,
        agent_host_path_mounts=(
            _g(row, "agent_host_path_mounts")
            if isinstance(_g(row, "agent_host_path_mounts"), list)
            else None
        ),
        agent_configmap_mounts=(
            _g(row, "agent_configmap_mounts")
            if isinstance(_g(row, "agent_configmap_mounts"), list)
            else None
        ),
        agent_pvc_mounts=(
            _g(row, "agent_pvc_mounts")
            if isinstance(_g(row, "agent_pvc_mounts"), list)
            else None
        ),
        main_container_id=_g(row, "main_container_id"),
        sidecar_container_ids=sidecar_ids,
        volumes=_g(row, "volumes") if isinstance(_g(row, "volumes"), list) else None,
        min_idle_services=_as_int(_g(row, "min_idle_services"), 0),
        service_concurrency=_as_int(_g(row, "service_concurrency"), 2),
        service_ttl=_as_int(_g(row, "service_ttl"), 300),
        message_timeout=_as_int(_g(row, "message_timeout"), 600),
        session_concurrency=_as_int(_g(row, "session_concurrency"), 3),
        session_ttl=_as_int(_g(row, "session_ttl"), 60),
        enabled=bool(_g(row, "enabled", True)),
        data=_g(row, "data"),
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ServiceConfigTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    @staticmethod
    def _build_row_for_create(
        body: ServiceConfigTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name,
            "description": body.description,
            "agent_image": body.agent_image or "",
            "namespace": body.namespace or "default",
            "node_name": body.node_name,
            "run_as_user": body.run_as_user,
            "run_as_group": body.run_as_group,
            "pod_name": body.pod_name or "agentserver",
            "container_name": body.container_name or "agent",
            "container_port": body.container_port,
            "port_name": body.port_name or "http",
            "sse_port": body.sse_port,
            "sse_path": body.sse_path or "/sse",
            "health_path": body.health_path or "/health",
            "agent_env": body.agent_env,
            "image_pull_policy": body.image_pull_policy,
            "kubeconfig": body.kubeconfig,
            "readiness_initial_delay": body.readiness_initial_delay,
            "readiness_period": body.readiness_period,
            "ready_timeout": body.ready_timeout,
            "ready_poll_interval": body.ready_poll_interval,
            "nfs_server": body.nfs_server,
            "nfs_path": body.nfs_path,
            "nfs_mount_path": body.nfs_mount_path,
            "agent_cpu_request": body.agent_cpu_request,
            "agent_memory_request": body.agent_memory_request,
            "agent_cpu_limit": body.agent_cpu_limit,
            "agent_memory_limit": body.agent_memory_limit,
            "sidecars": body.sidecars,
            "agent_host_path_mounts": body.agent_host_path_mounts,
            "agent_configmap_mounts": body.agent_configmap_mounts,
            "agent_pvc_mounts": body.agent_pvc_mounts,
            "main_container_id": body.main_container_id,
            "sidecar_container_ids": body.sidecar_container_ids,
            "volumes": body.volumes,
            "min_idle_services": body.min_idle_services,
            "service_concurrency": body.service_concurrency,
            "service_ttl": body.service_ttl,
            "message_timeout": body.message_timeout,
            "session_concurrency": body.session_concurrency,
            "session_ttl": body.session_ttl,
            "enabled": body.enabled,
            "data": body.data,
        }

    async def create(
        self,
        body: ServiceConfigTemplateCreateBody,
    ) -> ServiceConfigTemplateOut:
        template_uuid = new_uuid4()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        created = await self._handler.create(_TABLE, payload)
        return row_to_out(created)

    async def get(self, template_id: str) -> ServiceConfigTemplateOut | None:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return None
        return row_to_out(row)

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
            items = [
                row_to_out(r).model_dump(mode="json")
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
        items = [row_to_out(r).model_dump(mode="json") for r in rows]
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
            return row_to_out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, {"template_id": template_id})
        if existing is None:
            return None

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
        return row_to_out(row)

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(_TABLE, {"template_id": template_id})
        if row is None:
            return False
        await _assert_service_config_deletable(self._handler, template_id)
        return await self._handler.delete(_TABLE, {"template_id": template_id})
