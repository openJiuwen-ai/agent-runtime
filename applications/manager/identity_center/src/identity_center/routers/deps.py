"""鉴权依赖：OAuth2 Bearer + JWT 验签 → 当前用户 claims。

``oauth2_scheme`` 让 ``/docs`` 出现 Authorize（密码流，tokenUrl 指向 /token）。
本服务自身验签用本地公钥；claw_manager / 企业版 web 复用同样的验签逻辑（拿公钥）。

目录管理 API 要求管理面登录会话；Manager 的角色授权沿用原有实现。
"""

from __future__ import annotations

from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from identity_center.security.tokens import LoginSurface, decode_access_token

# tokenUrl 为相对路径，指向下方 /v1/auth/token；Swagger Authorize 用它走密码流。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="v1/auth/token")

_CRED_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid or expired token",
    headers={"WWW-Authenticate": "Bearer"},
)


def login_surface(request: Request, requested: LoginSurface | None = None) -> LoginSurface | None:
    # The two Web entry points overwrite this header before forwarding to Identity.
    ingress = request.headers.get("X-Jiuwen-Web-Surface")
    if ingress is None:
        return requested
    if ingress not in ("manager", "user") or (requested is not None and requested != ingress):
        raise HTTPException(status_code=403, detail="login surface mismatch")
    return ingress


async def get_current_claims(
    request: Request, token: Annotated[str, Depends(oauth2_scheme)],
) -> dict[str, Any]:
    try:
        claims = decode_access_token(token)
        if claims.get("surface") not in ("manager", "user"):
            raise _CRED_EXC
        login_surface(request, claims["surface"])
        return claims
    except jwt.PyJWTError as exc:
        raise _CRED_EXC from exc


_Claims = Annotated[dict[str, Any], Depends(get_current_claims)]


async def require_admin(claims: _Claims) -> dict[str, Any]:
    """历史名称保留。目录接口在 Identity 校验管理面登录身份。"""
    if claims.get("surface") != "manager" or not str(claims.get("sub") or "").strip():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return claims
