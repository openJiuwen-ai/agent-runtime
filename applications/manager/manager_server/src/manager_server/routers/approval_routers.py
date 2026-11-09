"""Manager 工作区配额审批 API（管理面审批）。"""

from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.approval import ApprovalService
from manager_server.core.approval.service import ApprovalOrderExpiredError
from manager_server.core.skill_upload.errors import SkillUploadError
from manager_server.core.skill_upload.service import SkillUploadService
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.auth_guards import get_current_user, require_permission
from manager_server.schemas.approval_schemas import ApprovalActionBody, ApprovalListQuery
from manager_server.schemas.common_schemas import ResponseModel

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_CurrentUser = Annotated[Any, Depends(get_current_user)]
_ReadApproval = Annotated[Any, Depends(require_permission("approval:read"))]
_ActApproval = Annotated[Any, Depends(require_permission("approval:act"))]

approval_router = APIRouter(prefix="/approvals")


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


def _user_id(user: Any) -> str:
    return str(getattr(user, "user_id", "") or "")


@approval_router.get("", response_model=ResponseModel)
async def list_approvals(
    handler: _Handler,
    user: _ReadApproval,
    query: Annotated[ApprovalListQuery, Depends()],
):
    """审批列表；``search`` 匹配单号、标题、申请说明。"""
    try:
        items = await ApprovalService(handler).list_orders(
            operator_id=_user_id(user),
            business_type=query.business_type,
            view=query.view,
            status=query.status,
            group_id=query.group_id,
            search=query.search,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok({"items": items})


@approval_router.get("/{order_num}", response_model=ResponseModel)
async def get_approval(order_num: str, handler: _Handler, _user: _ReadApproval):
    order = await ApprovalService(handler).get_order(order_num)
    if order is None:
        raise HTTPException(status_code=404, detail="approval order not found")
    return _ok(order)


@approval_router.get("/{order_num}/skill-package")
async def download_skill_package(
    order_num: str, handler: _Handler, _user: _ReadApproval
):
    """管理员下载审批单里的原始 Skill 包。

    manager 在集群内按 ``object_key`` 直读对象存储并校验 ``sha256``；浏览器不直连对象存储，
    因此内网 http MinIO 也能下载。
    """
    try:
        filename, content = await SkillUploadService(handler).read_package(
            order_num=order_num
        )
    except SkillUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _package_response(filename, content)


def _package_response(filename: str, content: bytes) -> Response:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "skill-package.zip"
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )


@approval_router.get("/{order_num}/skill-preview", response_model=ResponseModel)
async def preview_skill_package(
    order_num: str, handler: _Handler, _user: _ReadApproval
):
    """管理员只读预览审批单里的原始 Skill 包（manager 内存解包，不落盘）。"""
    try:
        data = await SkillUploadService(handler).read_preview(order_num=order_num)
    except SkillUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _ok(data)


@approval_router.get("/{order_num}/skill-preview/file")
async def preview_skill_package_file(
    order_num: str, path: str, handler: _Handler, _user: _ReadApproval
):
    """读取原始包内单个文件；图片/文本按媒体类型内联返回。"""
    try:
        content, media_type, truncated = await SkillUploadService(
            handler
        ).read_preview_file(order_num=order_num, path=path)
    except SkillUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    headers = {
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=60",
    }
    if truncated:
        headers["X-Preview-Truncated"] = "1"
    return Response(content=content, media_type=media_type, headers=headers)


@approval_router.post("/{order_num}/actions", response_model=ResponseModel)
async def act_on_approval(
    order_num: str,
    body: ApprovalActionBody,
    handler: _Handler,
    user: _ActApproval,
):
    try:
        result = await ApprovalService(handler).act(
            order_num=order_num,
            operator_id=_user_id(user),
            action=body.action,
            comment=body.comment,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApprovalOrderExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="approval order not found")
    return _ok(result)
