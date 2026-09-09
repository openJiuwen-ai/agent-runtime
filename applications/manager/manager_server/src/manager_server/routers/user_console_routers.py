"""用户控制台 API（当前用户可访问的 Agent 上下文）。

路径：``GET /v1/user-console/agent-contexts``。
身份来自 JWT（``get_current_user``）；组织 id 取 claims.groups。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.user_console import UserConsoleService
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.deps import get_current_user
from manager_server.schemas.common_schemas import ResponseModel

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_CurUser = Annotated[Any, Depends(get_current_user)]


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


user_console_router = APIRouter(dependencies=[Depends(get_current_user)])


@user_console_router.get("/agent-contexts", response_model=ResponseModel)
async def list_my_agent_contexts(
    handler: _Handler,
    user: _CurUser,
    authorization: Annotated[str | None, Header()] = None,
):
    """根据 instance_grant + instance_agent_resource 返回可访问组合。

    业务键：``bot_id`` / ``group_id`` / ``user_id``（均为 id）。
    展示字段：``agent_name`` / ``group_name``（另附 ``jiuwenclaw_id``）。
    """
    contexts = await UserConsoleService(handler).list_accessible_contexts(
        getattr(user, "user_id"),
        getattr(user, "groups", []),
        is_admin=bool(getattr(user, "is_admin", False)),
        authorization=authorization,
    )
    return _ok({"contexts": contexts})
