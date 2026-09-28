"""配额 API：实例级工作区配额策略。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.quota import WorkspaceQuotaPolicyService
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.auth_guards import require_permission
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.quota_schemas import (
    WorkspaceQuotaEffectiveQuery,
    WorkspaceQuotaPolicyCreateBody,
    WorkspaceQuotaPolicyListQuery,
    WorkspaceQuotaPolicyPatchBody,
)

quota_router = APIRouter()

_ReadQuota = Annotated[Any, Depends(require_permission("quota:read"))]
_WriteQuota = Annotated[Any, Depends(require_permission("quota:write"))]


def _workspace_quota_svc(handler: DBHandler) -> WorkspaceQuotaPolicyService:
    return WorkspaceQuotaPolicyService(handler)


def _actor(user: Any) -> str | None:
    uid = str(getattr(user, "user_id", "") or "").strip()
    return uid or None


@quota_router.get(
    "/{jiuwenclaw_id}/workspace-quota/policies",
    response_model=ResponseModel,
)
async def list_workspace_quota_policies(
    jiuwenclaw_id: str,
    _user: _ReadQuota,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[WorkspaceQuotaPolicyListQuery, Query()],
):
    data = await _workspace_quota_svc(handler).list_policies(
        cluster_id=jiuwenclaw_id,
        query=query,
    )
    return ResponseModel(code=200, message="success", data=data)


@quota_router.post(
    "/{jiuwenclaw_id}/workspace-quota/policies",
    response_model=ResponseModel,
)
async def create_workspace_quota_policy(
    jiuwenclaw_id: str,
    user: _WriteQuota,
    body: WorkspaceQuotaPolicyCreateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        data = await _workspace_quota_svc(handler).create(
            cluster_id=jiuwenclaw_id,
            policy_name=body.policy_name,
            policy_desc=body.policy_desc,
            match_expr=body.match_expr,
            priority=body.priority,
            limit_bytes=body.limit_bytes,
            soft_percent=body.soft_percent,
            hard_percent=body.hard_percent,
            source_order_num=body.source_order_num,
            actor_id=_actor(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=data)


@quota_router.patch(
    "/{jiuwenclaw_id}/workspace-quota/policies/{policy_id}",
    response_model=ResponseModel,
)
async def patch_workspace_quota_policy(
    jiuwenclaw_id: str,
    policy_id: str,
    user: _WriteQuota,
    body: WorkspaceQuotaPolicyPatchBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="at least one field is required")
    if "match_expr" in changes and changes["match_expr"] is None:
        raise HTTPException(status_code=400, detail="match_expr cannot be null")
    if "policy_name" in changes and changes["policy_name"] is None:
        raise HTTPException(status_code=400, detail="policy_name cannot be null")
    if "soft_percent" in changes and changes["soft_percent"] is None:
        raise HTTPException(status_code=400, detail="soft_percent cannot be null")
    if "hard_percent" in changes and changes["hard_percent"] is None:
        raise HTTPException(status_code=400, detail="hard_percent cannot be null")
    try:
        data = await _workspace_quota_svc(handler).update(
            policy_id,
            changes,
            actor_id=_actor(user),
            cluster_id=jiuwenclaw_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="workspace quota policy not found")
    return ResponseModel(code=200, message="success", data=data)


@quota_router.delete(
    "/{jiuwenclaw_id}/workspace-quota/policies/{policy_id}",
    response_model=ResponseModel,
)
async def delete_workspace_quota_policy(
    jiuwenclaw_id: str,
    policy_id: str,
    _user: _WriteQuota,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    try:
        await _workspace_quota_svc(handler).delete(policy_id, cluster_id=jiuwenclaw_id)
    except ValueError as exc:
        message = str(exc)
        status = 404 if "not found" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    return ResponseModel(code=200, message="success", data=None)


@quota_router.get(
    "/{jiuwenclaw_id}/workspace-quota/effective",
    response_model=ResponseModel,
)
async def get_workspace_quota_effective(
    jiuwenclaw_id: str,
    _user: _ReadQuota,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[WorkspaceQuotaEffectiveQuery, Depends()],
):
    data = await _workspace_quota_svc(handler).effective(
        cluster_id=jiuwenclaw_id,
        user_id=query.user_id,
        group_id=query.group_id,
        bot_id=query.bot_id,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="no matching workspace quota policy")
    return ResponseModel(code=200, message="success", data=data)
