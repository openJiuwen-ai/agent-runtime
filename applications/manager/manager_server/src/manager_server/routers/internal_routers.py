"""内部服务间 API（Gateway → Manager）。

这些接口不走 JWT 鉴权（``get_current_user``），而用共享密钥
``X-Internal-Token`` 头校验（环境变量 ``MANAGER_INTERNAL_TOKEN``）。
仅用于 Gateway 与 Manager 之间的可信服务调用，不暴露给前端。

当前接口：
- ``GET /v1/internal/skills/upload-permission/check?cluster_id=&user_id=``
  供 ``skills.import_upload`` WS RPC 在安装前复查当前用户的上传资格
  （WS 链路无原始 JWT，无法复用用户面 token 接口）。
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.identity_directory import IdentityDirectoryService
from manager_server.core.skill_upload import SkillUploadPermissionService
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler, get_identity_db_handler
from manager_server.schemas.common_schemas import ResponseModel

_Handler = Annotated[DBHandler, Depends(get_db_handler)]


def _verify_internal_token(x_internal_token: str) -> None:
    expected = str(settings.manager_internal_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="internal token not configured")
    received = str(x_internal_token or "").strip()
    if not received or not hmac.compare_digest(expected, received):
        raise HTTPException(status_code=401, detail="invalid internal token")


internal_router = APIRouter(prefix="/internal", tags=["Internal"])


@internal_router.get(
    "/skills/upload-permission/check",
    response_model=ResponseModel,
)
async def check_skill_upload_permission_by_user(
    handler: _Handler,
    cluster_id: Annotated[str, Query(min_length=1, max_length=64, description="企业实例 id")],
    user_id: Annotated[str, Query(min_length=1, max_length=128, description="当前用户 id")],
    x_internal_token: Annotated[str | None, Header()] = None,
) -> ResponseModel:
    """按 user_id 复查 Skill 上传资格（WS RPC ``skills.import_upload`` 用）。

    返回字段与用户面 ``GET /v1/user-console/skills/upload-permission`` 一致，
    但不依赖 JWT——以 ``X-Internal-Token`` 鉴权 + service 层直查授权表。
    组织授权需知用户所属组织：WS 链路无 JWT groups，故反查 identity 库的
    ``identity_user_org_membership`` 拿到用户组织 group_id 列表传入 effective。
    """
    _verify_internal_token(x_internal_token or "")
    groups = await IdentityDirectoryService(get_identity_db_handler()).list_user_org_ids(user_id)
    result = await SkillUploadPermissionService(handler).effective(
        cluster_id=cluster_id,
        user_id=user_id,
        groups=groups,
    )
    return ResponseModel(
        code=200,
        message="success",
        data={
            "allowed": bool(result.get("allowed")),
            "via": result.get("via"),
            "cluster_id": result.get("cluster_id"),
            "user_id": result.get("user_id"),
            "groups": result.get("groups") or [],
        },
    )
