"""实例资源 API：instance_agent_resource / instance_service_resource。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance_resource import InstanceAgentResourceService, InstanceServiceResourceService
from manager_server.core.instance_resource.runtime_config_sync import sync_runtime_config
from manager_server.infrastructure.db import get_db_handler
from manager_server.infrastructure.jiuwenclaw_id import validate_jiuwenclaw_id
from manager_server.routers.deps import require_admin
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.instance_resource_schemas import (
    CreateInstanceAgentResourceBody,
    CreateInstanceServiceResourceBody,
    ListInstanceAgentResourcesQuery,
    ListInstanceServiceResourcesQuery,
    UpdateInstanceAgentResourceBody,
    UpdateInstanceServiceResourceBody,
)

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_Admin = Annotated[Any, Depends(require_admin)]

instance_resource_router = APIRouter(dependencies=[Depends(require_admin)])


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


def _granted_by(user: Any) -> str | None:
    return str(getattr(user, "user_id", "") or "") or None


async def _jid(handler: DBHandler, jiuwenclaw_id: str) -> str:
    try:
        return await validate_jiuwenclaw_id(handler, jiuwenclaw_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _map_write_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@instance_resource_router.get("/instances/{jiuwenclaw_id}/agent-resources", response_model=ResponseModel)
async def list_instance_agent_resources(
    jiuwenclaw_id: str,
    handler: _Handler,
    q: Annotated[ListInstanceAgentResourcesQuery, Depends()],
):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok(
        {
            **await InstanceAgentResourceService(handler).list_instance_agent_resources(
                jid,
                page=q.page,
                page_size=q.page_size,
                search=q.search,
                enabled=q.enabled,
                sort_by=q.sort_by,
                sort_order=q.sort_order,
            )
        }
    )


@instance_resource_router.post("/instances/{jiuwenclaw_id}/agent-resources", response_model=ResponseModel)
async def create_instance_agent_resource(
    jiuwenclaw_id: str, body: CreateInstanceAgentResourceBody, handler: _Handler, user: _Admin
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        items = await InstanceAgentResourceService(handler).create_resource(
            jid, body.ref_template_id, body.match_exprs,
            resource_name=body.resource_name, resource_desc=body.resource_desc,
            granted_by=_granted_by(user), enabled=body.enabled,
            expires_at=body.expires_at, data=body.data,
        )
        await sync_runtime_config(handler, jid)
        return _ok({"items": items})
    except (ValueError, LookupError) as e:
        raise _map_write_error(e) from e


@instance_resource_router.patch(
    "/instances/{jiuwenclaw_id}/agent-resources/{resource_id}",
    response_model=ResponseModel,
)
async def update_instance_agent_resource(
    jiuwenclaw_id: str,
    resource_id: str,
    body: UpdateInstanceAgentResourceBody,
    handler: _Handler,
    user: _Admin,
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        items = await InstanceAgentResourceService(handler).replace_resource(
            jid, resource_id, body.match_exprs,
            resource_name=body.resource_name, resource_desc=body.resource_desc,
            granted_by=_granted_by(user), enabled=body.enabled,
            expires_at=body.expires_at, data=body.data,
        )
        await sync_runtime_config(handler, jid)
        return _ok({"items": items})
    except (ValueError, LookupError) as e:
        raise _map_write_error(e) from e


@instance_resource_router.delete(
    "/instances/{jiuwenclaw_id}/agent-resources/{resource_id}",
    response_model=ResponseModel,
)
async def remove_instance_agent_resource(jiuwenclaw_id: str, resource_id: str, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    removed = await InstanceAgentResourceService(handler).remove_resource(jid, resource_id)
    if not removed:
        raise HTTPException(status_code=404, detail="instance agent resource not found")
    await sync_runtime_config(handler, jid)
    return _ok({"removed": True})


@instance_resource_router.get("/instances/{jiuwenclaw_id}/service-resources", response_model=ResponseModel)
async def list_instance_service_resources(
    jiuwenclaw_id: str,
    handler: _Handler,
    q: Annotated[ListInstanceServiceResourcesQuery, Depends()],
):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok(
        {
            **await InstanceServiceResourceService(handler).list_instance_resources(
                jid,
                page=q.page,
                page_size=q.page_size,
                search=q.search,
                enabled=q.enabled,
                sort_by=q.sort_by,
                sort_order=q.sort_order,
            )
        }
    )


@instance_resource_router.post("/instances/{jiuwenclaw_id}/service-resources", response_model=ResponseModel)
async def create_instance_service_resource(
    jiuwenclaw_id: str,
    body: CreateInstanceServiceResourceBody,
    handler: _Handler,
    user: _Admin,
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        items = await InstanceServiceResourceService(handler).create_resource(
            jid, body.ref_template_id, body.match_exprs,
            resource_name=body.resource_name, resource_desc=body.resource_desc,
            priority=body.priority, granted_by=_granted_by(user),
            enabled=body.enabled, expires_at=body.expires_at, data=body.data,
        )
        await sync_runtime_config(handler, jid)
        return _ok({"items": items})
    except (ValueError, LookupError) as e:
        raise _map_write_error(e) from e


@instance_resource_router.patch(
    "/instances/{jiuwenclaw_id}/service-resources/{resource_id}",
    response_model=ResponseModel,
)
async def update_instance_service_resource(
    jiuwenclaw_id: str,
    resource_id: str,
    body: UpdateInstanceServiceResourceBody,
    handler: _Handler,
    user: _Admin,
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        items = await InstanceServiceResourceService(handler).replace_resource(
            jid, resource_id, body.match_exprs,
            resource_name=body.resource_name, resource_desc=body.resource_desc,
            priority=body.priority, granted_by=_granted_by(user),
            enabled=body.enabled, expires_at=body.expires_at, data=body.data,
        )
        await sync_runtime_config(handler, jid)
        return _ok({"items": items})
    except (ValueError, LookupError) as e:
        raise _map_write_error(e) from e


@instance_resource_router.delete(
    "/instances/{jiuwenclaw_id}/service-resources/{resource_id}",
    response_model=ResponseModel,
)
async def remove_instance_service_resource(jiuwenclaw_id: str, resource_id: str, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    removed = await InstanceServiceResourceService(handler).remove_resource(jid, resource_id)
    if not removed:
        raise HTTPException(status_code=404, detail="instance service resource not found")
    await sync_runtime_config(handler, jid)
    return _ok({"removed": True})
