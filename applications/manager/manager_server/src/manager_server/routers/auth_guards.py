"""鉴权依赖：校验认证服务签发的 RS256 JWT(资源服务器),解析当前用户 + 平台管理员守卫。

不再查库发 token；从 ``Authorization: Bearer <jwt>`` 本地验签，principal 来自 claims
（sub / groups / name）。产品权限与平台管理员资格一律查 Manager 本地授权表，
不再使用 identity_user.is_admin。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, HTTPException
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.authz import AuthzService
from manager_server.infrastructure.db import get_db_handler
from manager_server.security.jwt_verify import decode_token


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        claims = await decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc
    return SimpleNamespace(
        user_id=str(claims.get("sub") or ""),
        # 兼容旧代码读取；权限判定不得依赖该字段
        is_admin=False,
        groups=list(claims.get("groups") or []),
        display_name=claims.get("name"),
    )


async def require_admin(
    user: Annotated[Any, Depends(get_current_user)],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
) -> Any:
    """平台级管理 API 守卫：platform_admin 或任意 admin 类型角色。"""
    user_id = str(getattr(user, "user_id", "") or "")
    if not user_id or not await AuthzService(handler).is_platform_admin(user_id):
        raise HTTPException(status_code=403, detail="admin required")
    return user


def require_permission(permission_id: str):
    """构造产品权限依赖；JWT 只负责身份，权限从 Manager 本地授权表解析。"""

    async def dependency(
        user: Annotated[Any, Depends(get_current_user)],
        handler: Annotated[DBHandler, Depends(get_db_handler)],
    ) -> Any:
        if not await AuthzService(handler).has_permission(user, permission_id):
            raise HTTPException(
                status_code=403,
                detail=f"permission required: {permission_id}",
            )
        return user

    return dependency


CurrentUser = Annotated[Any, Depends(get_current_user)]
AdminUser = Annotated[Any, Depends(require_admin)]
