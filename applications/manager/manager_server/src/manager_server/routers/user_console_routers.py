"""用户控制台 API（当前用户可见 Agent）。

路径：``GET /v1/user-console/agents``。
身份/组织目录在认证服务；模板与实例资源在各自模块。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.user_console import GroupAccessDeniedError, UserConsoleService
from manager_server.core.instance_access import InstanceGrantService
from manager_server.core.instance_access.instance_grant_service import SUBJECT_ORG, SUBJECT_USER
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.deps import get_current_user
from manager_server.schemas.common_schemas import ResponseModel

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_CurUser = Annotated[Any, Depends(get_current_user)]


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


user_console_router = APIRouter(dependencies=[Depends(get_current_user)])


@user_console_router.get("/agents", response_model=ResponseModel)
async def list_my_agents(
    handler: _Handler, user: _CurUser, group_id: str = Query(...),
    jiuwenclaw_id: str | None = Query(default=None),
):
    try:
        agents = await UserConsoleService(handler).list_visible_agents(
            getattr(user, "user_id"), group_id, getattr(user, "groups", []),
            jiuwenclaw_id=(jiuwenclaw_id or settings.jiuwenclaw_id),
            is_admin=bool(getattr(user, "is_admin", False)),
        )
    except GroupAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail="group access denied") from exc
    return _ok({"agents": agents})


@user_console_router.get("/gateways", response_model=ResponseModel)
async def list_my_gateways(handler: _Handler, user: _CurUser):
    """返回当前用户可选择的 Gateway 及其管理面下发资源。"""
    user_id = str(getattr(user, "user_id", "") or "")
    groups = {str(x) for x in (getattr(user, "groups", []) or []) if str(x).strip()}
    grants = InstanceGrantService(handler)
    user_map = await grants.list_instances_for(SUBJECT_USER, [user_id])
    allowed_ids = set(user_map.get(user_id, []))
    if groups:
        org_map = await grants.list_instances_for(SUBJECT_ORG, list(groups))
        for ids in org_map.values():
            allowed_ids.update(ids)
    rows = []
    for jid in sorted(allowed_ids):
        row = await handler.get("instance_info", {"jiuwenclaw_id": jid})
        if row is None:
            continue
        data = getattr(row, "data", None) if row is not None else None
        data = data if isinstance(data, dict) else {}
        rows.append({
            "jiuwenclaw_id": jid,
            "jiuwenclaw_name": str(getattr(row, "jiuwenclaw_name", jid) or jid),
            "gateway_status": str(
                getattr(row, "gateway_status", "unknown") or "unknown"
            ),
            "runtime_status": str(
                getattr(row, "runtime_status", "unknown") or "unknown"
            ),
            "space_id": str(getattr(row, "space_id", "") or ""),
            "gateway_endpoint": (
                str(getattr(row, "gateway_config_host", None) or "").strip()
                or str(data.get("gateway_endpoint") or "").strip()
                or None
            ),
        })
    return _ok({"gateways": rows})
