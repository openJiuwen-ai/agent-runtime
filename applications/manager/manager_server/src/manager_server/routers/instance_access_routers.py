"""实例准入 API：用户/组织 ↔ 实例授权（instance_grant）。

设计:目录(用户/组织)保持全局,"谁能用哪个实例"落在
``instance_grant``（``subject_type`` = user / org；合并原 user_gateway / org_gateway）。
Agent / 服务资源见 instance_resource 模块；用户/组织目录 CRUD 在认证服务(jiuwenclaw_identity)。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance_access import org_gateway_service, user_gateway_service
from manager_server.infrastructure.db import get_db_handler
from manager_server.infrastructure.jiuwenclaw_id import validate_jiuwenclaw_id
from manager_server.routers.deps import require_admin
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.instance_access_schemas import (
    InstanceBindBody,
    InstanceGrantUpdateBody,
    InstanceUnbindBody,
)

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_Admin = Annotated[Any, Depends(require_admin)]


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


async def _jid(handler: DBHandler, jiuwenclaw_id: str) -> str:
    """校验实例存在并返回规范化 id；不存在 → 404。"""
    try:
        return await validate_jiuwenclaw_id(handler, jiuwenclaw_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _granted_by(user: Any) -> str | None:
    return str(getattr(user, "user_id", "") or "") or None


instance_grant_router = APIRouter(dependencies=[Depends(require_admin)])


# ---------- 用户 ↔ 实例 ----------
@instance_grant_router.get("/{jiuwenclaw_id}/users", response_model=ResponseModel)
async def list_instance_user_grants(jiuwenclaw_id: str, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    items = await user_gateway_service(handler).list_grants(jid)
    return _ok({
        "items": items,
        "user_ids": [str(x.get("subject_id") or "") for x in items],
    })


@instance_grant_router.post("/{jiuwenclaw_id}/users", response_model=ResponseModel)
async def grant_instance_users(
    jiuwenclaw_id: str, body: InstanceBindBody, handler: _Handler, user: _Admin
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        return _ok(
            await user_gateway_service(handler).bind(
                jid,
                body.ids,
                granted_by=_granted_by(user),
                login_policy=body.login_policy,
                expires_at=body.expires_at,
                enabled=body.enabled,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@instance_grant_router.patch("/{jiuwenclaw_id}/users/{user_id}", response_model=ResponseModel)
async def update_instance_user_grant(
    jiuwenclaw_id: str,
    user_id: str,
    body: InstanceGrantUpdateBody,
    handler: _Handler,
    user: _Admin,
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        row = await user_gateway_service(handler).update_grant(
            jid,
            user_id,
            enabled=body.enabled,
            login_policy=body.login_policy,
            expires_at=body.expires_at,
            clear_expires_at=body.clear_expires_at,
            granted_by=_granted_by(user),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if row is None:
        raise HTTPException(status_code=404, detail="instance grant not found")
    return _ok(row)


@instance_grant_router.delete("/{jiuwenclaw_id}/users", response_model=ResponseModel)
async def revoke_instance_user_grants(jiuwenclaw_id: str, body: InstanceUnbindBody, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok(await user_gateway_service(handler).unbind(jid, body.ids))


# ---------- 组织 ↔ 实例 ----------
@instance_grant_router.get("/{jiuwenclaw_id}/orgs", response_model=ResponseModel)
async def list_instance_org_grants(jiuwenclaw_id: str, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    items = await org_gateway_service(handler).list_grants(jid)
    return _ok({
        "items": items,
        "group_ids": [str(x.get("subject_id") or "") for x in items],
    })


@instance_grant_router.post("/{jiuwenclaw_id}/orgs", response_model=ResponseModel)
async def grant_instance_orgs(
    jiuwenclaw_id: str, body: InstanceBindBody, handler: _Handler, user: _Admin
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        return _ok(
            await org_gateway_service(handler).bind(
                jid,
                body.ids,
                granted_by=_granted_by(user),
                login_policy=body.login_policy,
                expires_at=body.expires_at,
                enabled=body.enabled,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@instance_grant_router.patch("/{jiuwenclaw_id}/orgs/{group_id}", response_model=ResponseModel)
async def update_instance_org_grant(
    jiuwenclaw_id: str,
    group_id: str,
    body: InstanceGrantUpdateBody,
    handler: _Handler,
    user: _Admin,
):
    jid = await _jid(handler, jiuwenclaw_id)
    try:
        row = await org_gateway_service(handler).update_grant(
            jid,
            group_id,
            enabled=body.enabled,
            login_policy=body.login_policy,
            expires_at=body.expires_at,
            clear_expires_at=body.clear_expires_at,
            granted_by=_granted_by(user),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if row is None:
        raise HTTPException(status_code=404, detail="instance grant not found")
    return _ok(row)


@instance_grant_router.delete("/{jiuwenclaw_id}/orgs", response_model=ResponseModel)
async def revoke_instance_org_grants(jiuwenclaw_id: str, body: InstanceUnbindBody, handler: _Handler):
    jid = await _jid(handler, jiuwenclaw_id)
    return _ok(await org_gateway_service(handler).unbind(jid, body.ids))


# 反查（挂在 /v1 根，不带 /instances 前缀）：一批实体各授权了哪些实例。
gateway_lookup_router = APIRouter(dependencies=[Depends(require_admin)])


@gateway_lookup_router.get("/user-gateways", response_model=ResponseModel)
async def list_user_instance_grants(handler: _Handler, user_ids: str = Query(default="")):
    ids = [x for x in user_ids.split(",") if x.strip()]
    return _ok({"bindings": await user_gateway_service(handler).list_instances_for(ids)})


@gateway_lookup_router.get("/org-gateways", response_model=ResponseModel)
async def list_org_instance_grants(handler: _Handler, group_ids: str = Query(default="")):
    ids = [x for x in group_ids.split(",") if x.strip()]
    return _ok({"bindings": await org_gateway_service(handler).list_instances_for(ids)})
