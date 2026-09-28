"""Manager 工作区配额审批 API（管理面审批）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.approval import ApprovalService
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="approval order not found")
    return _ok(result)
