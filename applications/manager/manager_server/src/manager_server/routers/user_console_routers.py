"""用户控制台 API（当前用户可访问的 Agent 上下文 + 用户面选路）。

路径：
- ``GET /v1/user-console/agent-contexts``
- ``POST /v1/user-console/active-cluster``（写入 Cookie ``jiuwenclaw_id``）
- ``GET /v1/user-console/user-face-upstream``（nginx auth_request 解析上游）
身份来自 JWT（``get_current_user``）；组织 id 取 claims.groups。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from openjiuwen_runtime.foundation.db.handler import DBHandler
from pydantic import BaseModel, Field

from manager_server.core.user_console import UserConsoleService
from manager_server.core.user_console.user_face_upstream import (
    JIUWENCLAW_ID_COOKIE,
    resolve_user_face_upstreams,
)
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.deps import get_current_user
from manager_server.schemas.common_schemas import ResponseModel

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_CurUser = Annotated[Any, Depends(get_current_user)]


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


class ActiveClusterBody(BaseModel):
    jiuwenclaw_id: str = Field(..., min_length=1, max_length=64)


user_console_router = APIRouter(dependencies=[Depends(get_current_user)])


@user_console_router.get("/agent-contexts", response_model=ResponseModel)
async def list_my_agent_contexts(
    handler: _Handler,
    user: _CurUser,
    authorization: Annotated[str | None, Header()] = None,
):
    """根据 instance_grant + instance_agent_resource 返回可访问组合。

    业务键：``bot_id`` / ``group_id`` / ``user_id``（均为 id）。
    返回 ``agent_name`` / ``group_name`` 展示字段，以及 ``jiuwenclaw_id``。
    """
    contexts = await UserConsoleService(handler).list_accessible_contexts(
        getattr(user, "user_id"),
        getattr(user, "groups", []),
        is_admin=bool(getattr(user, "is_admin", False)),
        authorization=authorization,
    )
    return _ok({"contexts": contexts})


@user_console_router.post("/active-cluster", response_model=ResponseModel)
async def set_active_cluster(
    body: ActiveClusterBody,
    response: Response,
    handler: _Handler,
    user: _CurUser,
):
    """设置用户面动态反代 Cookie（``jiuwenclaw_id``）。"""
    jid = body.jiuwenclaw_id.strip()
    allowed = await UserConsoleService(handler).user_can_access_instance(
        getattr(user, "user_id"),
        jid,
        getattr(user, "groups", []),
        is_admin=bool(getattr(user, "is_admin", False)),
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="instance not admitted")
    response.set_cookie(
        key=JIUWENCLAW_ID_COOKIE,
        value=jid,
        path="/",
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return _ok({"jiuwenclaw_id": jid})


@user_console_router.get("/user-face-upstream")
async def resolve_user_face_upstream(
    handler: _Handler,
    user: _CurUser,
    jiuwenclaw_id: Annotated[str | None, Cookie(alias=JIUWENCLAW_ID_COOKIE)] = None,
) -> Response:
    """鉴权 + 按 Cookie 下发用户面上游 origin（供 nginx ``auth_request_set``）。

    响应头：``X-User-Web-Upstream`` / ``X-Gateway-Http-Upstream`` / ``X-Gateway-Ws-Upstream``。
    """
    try:
        upstreams = await resolve_user_face_upstreams(
            handler,
            user_id=str(getattr(user, "user_id", "") or ""),
            groups=list(getattr(user, "groups", []) or []),
            is_admin=bool(getattr(user, "is_admin", False)),
            jiuwenclaw_id=jiuwenclaw_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        # auth_request 对非 401/403 会变成 500；实例缺失/上游不全统一 403
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    headers = upstreams.as_headers()
    headers["Cache-Control"] = "private, max-age=5"
    return Response(status_code=200, content=b"", headers=headers)
