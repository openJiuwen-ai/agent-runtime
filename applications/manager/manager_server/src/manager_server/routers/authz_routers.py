"""Manager 产品角色、权限与当前用户授权 API。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.authz import AuthzService
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.auth_guards import get_current_user, require_permission
from manager_server.schemas.authz_schemas import (
    PermissionListQuery,
    RoleCreateBody,
    RoleListQuery,
    RoleUpdateBody,
    RoleUserAssignBody,
    RoleUserListQuery,
    RoleUsersBody,
)
from manager_server.schemas.common_schemas import ResponseModel

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_CurrentUser = Annotated[Any, Depends(get_current_user)]
_ReadRole = Annotated[Any, Depends(require_permission("iam:role:read"))]
_WriteRole = Annotated[Any, Depends(require_permission("iam:role:write"))]

authz_router = APIRouter(prefix="/authz")


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


def _operator_id(user: Any) -> str:
    return str(getattr(user, "user_id", "") or "")


@authz_router.get("/me", response_model=ResponseModel)
async def get_my_authorization(handler: _Handler, user: _CurrentUser):
    """返回管理面路由和按钮所需授权（仅角色指派，不读取 is_admin）。"""
    service = AuthzService(handler)
    user_id = _operator_id(user)
    permissions = sorted(await service.permissions_for_user(user_id))
    role_ids = await service.role_ids_for_user(user_id)
    is_platform_admin = await service.is_platform_admin(user_id)
    return _ok(
        {
            "user_id": user_id,
            "role_ids": role_ids,
            "permissions": permissions,
            "manager_access": await service.has_manager_access(user_id),
            "is_platform_admin": is_platform_admin,
        }
    )


@authz_router.get("/permissions", response_model=ResponseModel)
async def list_permissions(
    handler: _Handler,
    _user: _ReadRole,
    query: Annotated[PermissionListQuery, Query()],
):
    return _ok({"items": await AuthzService(handler).list_permissions(query)})


@authz_router.get("/roles", response_model=ResponseModel)
async def list_roles(
    handler: _Handler,
    _user: _ReadRole,
    query: Annotated[RoleListQuery, Query()],
):
    return _ok(await AuthzService(handler).list_roles(query))


@authz_router.get("/roles/{role_id}", response_model=ResponseModel)
async def get_role(role_id: str, handler: _Handler, _user: _ReadRole):
    role = await AuthzService(handler).get_role(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    return _ok(role)


@authz_router.post("/roles", response_model=ResponseModel)
async def create_role(body: RoleCreateBody, handler: _Handler, user: _WriteRole):
    try:
        role = await AuthzService(handler).create_role(
            role_id=body.role_id,
            name=body.name,
            description=body.description,
            scope=body.scope,
            permission_ids=body.permission_ids,
            operator_id=_operator_id(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(role)


@authz_router.patch("/roles/{role_id}", response_model=ResponseModel)
async def update_role(
    role_id: str,
    body: RoleUpdateBody,
    handler: _Handler,
    user: _WriteRole,
):
    try:
        role = await AuthzService(handler).update_role(
            role_id,
            name=body.name,
            description=body.description,
            scope=body.scope,
            enabled=body.enabled,
            permission_ids=body.permission_ids,
            operator_id=_operator_id(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    return _ok(role)


@authz_router.delete("/roles/{role_id}", response_model=ResponseModel)
async def delete_role(role_id: str, handler: _Handler, user: _WriteRole):
    try:
        deleted = await AuthzService(handler).delete_role(
            role_id,
            _operator_id(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="role not found")
    return _ok({"role_id": role_id, "deleted": True})


@authz_router.get("/roles/{role_id}/users", response_model=ResponseModel)
async def list_role_users(
    role_id: str,
    handler: _Handler,
    _user: _ReadRole,
    query: Annotated[RoleUserListQuery, Query()],
):
    result = await AuthzService(handler).list_role_users(role_id, query)
    if result is None:
        raise HTTPException(status_code=404, detail="role not found")
    return _ok(result)


@authz_router.post("/roles/{role_id}/users", response_model=ResponseModel)
async def assign_role_users(
    role_id: str,
    body: RoleUserAssignBody,
    handler: _Handler,
    user: _WriteRole,
):
    try:
        role = await AuthzService(handler).assign_role_users(
            role_id,
            body.user_ids,
            _operator_id(user),
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    return _ok(role)


@authz_router.put("/roles/{role_id}/users", response_model=ResponseModel)
async def replace_role_users(
    role_id: str,
    body: RoleUsersBody,
    handler: _Handler,
    user: _WriteRole,
):
    role = await AuthzService(handler).replace_role_users(
        role_id,
        body.user_ids,
        _operator_id(user),
    )
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    return _ok(role)
