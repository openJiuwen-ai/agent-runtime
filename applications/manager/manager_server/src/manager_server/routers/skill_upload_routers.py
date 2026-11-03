"""Skill 上传授权管理 API（管理员直授）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.skill_upload import SkillUploadPermissionService
from manager_server.infrastructure.db import get_db_handler
from manager_server.infrastructure.jiuwenclaw_id import validate_jiuwenclaw_id
from manager_server.routers.auth_guards import require_permission
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.skill_upload_schemas import (
    SkillUploadGrantBindBody,
    SkillUploadGrantListQuery,
    SkillUploadGrantRevokeBody,
    SkillUploadGrantUpdateBody,
)

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_ReadPermission = Annotated[Any, Depends(require_permission("skill:upload:read"))]
_WritePermission = Annotated[Any, Depends(require_permission("skill:upload:grant"))]

skill_upload_router = APIRouter(prefix="/skills/upload-permissions")


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


def _operator_id(user: Any) -> str:
    return str(getattr(user, "user_id", "") or "")


@skill_upload_router.get("", response_model=ResponseModel)
async def list_skill_upload_permissions(
    handler: _Handler,
    _user: _ReadPermission,
    query: Annotated[SkillUploadGrantListQuery, Depends()],
):
    return _ok(await SkillUploadPermissionService(handler).list_permissions(query))


@skill_upload_router.post("", response_model=ResponseModel)
async def bind_skill_upload_permissions(
    body: SkillUploadGrantBindBody,
    handler: _Handler,
    user: _WritePermission,
):
    try:
        await validate_jiuwenclaw_id(handler, body.cluster_id)
        result = await SkillUploadPermissionService(handler).bind(
            cluster_id=body.cluster_id,
            subject_type=body.subject_type,
            ids=body.ids,
            operator_id=_operator_id(user),
            expires_at=body.expires_at,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(result)


@skill_upload_router.patch("/{permission_id}", response_model=ResponseModel)
async def update_skill_upload_permission(
    permission_id: int,
    body: SkillUploadGrantUpdateBody,
    handler: _Handler,
    user: _WritePermission,
):
    try:
        permission = await SkillUploadPermissionService(handler).update_permission(
            permission_id,
            enabled=body.enabled,
            expires_at=body.expires_at,
            clear_expires_at=body.clear_expires_at,
            operator_id=_operator_id(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if permission is None:
        raise HTTPException(status_code=404, detail="skill upload permission not found")
    return _ok(permission)


@skill_upload_router.delete("", response_model=ResponseModel)
async def revoke_skill_upload_permissions(
    body: SkillUploadGrantRevokeBody,
    handler: _Handler,
    user: _WritePermission,
):
    try:
        await validate_jiuwenclaw_id(handler, body.cluster_id)
        result = await SkillUploadPermissionService(handler).revoke(
            cluster_id=body.cluster_id,
            subject_type=body.subject_type,
            ids=body.ids,
            operator_id=_operator_id(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(result)
