"""用户控制台 API（当前用户可见 Agent）。

路径：``GET /v1/user-console/agents``。
身份/组织目录在认证服务；模板与实例资源在各自模块。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.user_console import UserConsoleService
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
async def list_my_agents(handler: _Handler, user: _CurUser, group_id: str = Query(...)):
    agents = await UserConsoleService(handler).list_visible_agents(
        getattr(user, "user_id"), group_id, getattr(user, "groups", []),
        jiuwenclaw_id=settings.jiuwenclaw_id,
        is_admin=bool(getattr(user, "is_admin", False)),
    )
    return _ok({"agents": agents})
