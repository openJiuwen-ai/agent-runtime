"""管理面身份目录查询 API（admin 守卫）。

供 skill_upload 授权选择器等管理面 UI 拉组织/用户目录。数据来自 identity 库
（只读），不在 manager-server 维护目录写入——目录 CRUD 在 identity_center。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.identity_directory import IdentityDirectoryService
from manager_server.infrastructure.db import get_identity_db_handler
from manager_server.routers.auth_guards import require_admin
from manager_server.schemas.common_schemas import ResponseModel

_Admin = Annotated[Any, Depends(require_admin)]

iam_directory_router = APIRouter(dependencies=[Depends(require_admin)])


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


@iam_directory_router.get("/orgs", response_model=ResponseModel)
async def list_directory_orgs(
    _admin: _Admin,
    search: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> ResponseModel:
    """列出组织目录（group_id/display_name/status）。"""
    items = await IdentityDirectoryService(get_identity_db_handler()).list_orgs(
        search=search, status=status
    )
    return _ok({"items": items, "total": len(items)})


@iam_directory_router.get("/users", response_model=ResponseModel)
async def list_directory_users(
    _admin: _Admin,
    search: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> ResponseModel:
    """列出用户目录（user_id/display_name/is_admin/status）。"""
    items = await IdentityDirectoryService(get_identity_db_handler()).list_users(
        search=search, status=status
    )
    return _ok({"items": items, "total": len(items)})
