"""用户控制台 API（当前用户可访问的 Agent 上下文 + 用户面选路 + 扩容申请）。

路径：
- ``GET /v1/user-console/agent-contexts``
- ``POST /v1/user-console/active-cluster``（写入 Cookie ``jiuwenclaw_id``）
- ``DELETE /v1/user-console/active-cluster``（清除 Cookie ``jiuwenclaw_id``）
- ``GET /v1/user-console/user-face-upstream``（nginx auth_request 解析上游）
- ``POST /v1/user-console/workspace/expand-requests``
- ``GET /v1/user-console/approvals/candidates``
- ``GET /v1/user-console/approvals/mine``
- ``POST /v1/user-console/approvals/{order_num}/cancel``
身份来自 JWT（``get_current_user``）；组织 id 取 claims.groups。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response
from openjiuwen_runtime.foundation.db.handler import DBHandler
from pydantic import BaseModel, Field

from manager_server.core.approval import ApprovalService
from manager_server.core.skill_upload import SKILL_UPLOAD_PERMISSION, resolve_upload_permission
from manager_server.core.user_console import UserConsoleService
from manager_server.core.user_console.user_face_upstream import (
    JIUWENCLAW_ID_COOKIE,
    resolve_user_face_upstreams,
)
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.auth_guards import get_current_user
from manager_server.schemas.approval_schemas import UserConsoleExpandSubmitBody
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.skill_upload_schemas import SkillUploadPermissionSubmitBody

WORKSPACE_QUOTA_FEATURE_DISABLED = "WORKSPACE_QUOTA_FEATURE_DISABLED"
SKILL_UPLOAD_FEATURE_DISABLED = "SKILL_UPLOAD_FEATURE_DISABLED"

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_CurUser = Annotated[Any, Depends(get_current_user)]


def _ok(data: Any = None) -> ResponseModel:
    return ResponseModel(code=200, message="success", data=data)


def _user_id(user: Any) -> str:
    return str(getattr(user, "user_id", "") or "")


class ActiveClusterBody(BaseModel):
    jiuwenclaw_id: str = Field(..., min_length=1, max_length=64)


user_console_router = APIRouter()
# Logout cleanup must also work with expired tokens; only this cookie-only
# endpoint is mounted outside the authenticated business subtree.
public_user_console_router = APIRouter()


@user_console_router.get(
    "/agent-contexts",
    response_model=ResponseModel,
    dependencies=[Depends(get_current_user)],
)
async def list_my_agent_contexts(
    handler: _Handler,
    user: _CurUser,
    authorization: Annotated[str | None, Header()] = None,
):
    """根据 instance_grant + instance_agent_resource 返回可访问组合。

    业务键：``bot_id`` / ``group_id`` / ``user_id``（均为 id）。
    返回 ``agent_name`` / ``group_name`` / ``jiuwenclaw_name`` 展示字段，以及 ``jiuwenclaw_id``。
    """
    contexts = await UserConsoleService(handler).list_accessible_contexts(
        getattr(user, "user_id"),
        getattr(user, "groups", []),
        authorization=authorization,
    )
    return _ok({"contexts": contexts})


@user_console_router.post(
    "/active-cluster",
    response_model=ResponseModel,
    dependencies=[Depends(get_current_user)],
)
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


@public_user_console_router.delete("/active-cluster", response_model=ResponseModel)
async def clear_active_cluster(response: Response):
    """清除用户面动态反代 Cookie（登出 / 换用户 / 无上下文进入 /chat 前调用）。

    不要求登录：登出时 token 可能已失效，但仍须清掉 HttpOnly ``jiuwenclaw_id``，
    否则下一用户会被 ``user-face-upstream`` 按旧实例鉴权拒绝（403）。
    """
    response.delete_cookie(
        key=JIUWENCLAW_ID_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return _ok({"cleared": True})


@user_console_router.get(
    "/user-face-upstream",
    dependencies=[Depends(get_current_user)],
)
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
            jiuwenclaw_id=jiuwenclaw_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        # auth_request 对非 401/403 会变成 500；实例缺失/上游不全统一 403
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    headers = upstreams.as_headers()
    headers["Cache-Control"] = "no-store"
    return Response(status_code=200, content=b"", headers=headers)


@user_console_router.post(
    "/workspace/expand-requests",
    response_model=ResponseModel,
)
async def submit_workspace_expand(
    body: UserConsoleExpandSubmitBody,
    handler: _Handler,
    user: _CurUser,
    authorization: Annotated[str | None, Header()] = None,
):
    """用户面发起扩容：校验上下文准入，服务端解析生效配额后建单。"""
    if not settings.workspace_quota_enabled:
        raise HTTPException(status_code=403, detail=WORKSPACE_QUOTA_FEATURE_DISABLED)
    uid = _user_id(user)
    allowed = await UserConsoleService(handler).user_can_access_context(
        uid,
        jiuwenclaw_id=body.jiuwenclaw_id,
        group_id=body.group_id,
        bot_id=body.bot_id,
        groups=list(getattr(user, "groups", []) or []),
        authorization=authorization,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="context not admitted")
    try:
        result = await ApprovalService(handler).submit_from_user_console(
            applicant_id=uid,
            group_id=body.group_id,
            bot_id=body.bot_id,
            cluster_id=body.jiuwenclaw_id,
            requested_limit_bytes=body.requested_limit_bytes,
            reason=body.reason,
            used_bytes=body.used_bytes,
            approver_id=body.approver_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(result)


@user_console_router.get(
    "/skills/upload-permission",
    response_model=ResponseModel,
)
async def get_skill_upload_permission(
    handler: _Handler,
    user: _CurUser,
    jiuwenclaw_id: Annotated[str | None, Query(max_length=64)] = None,
    active_cluster: Annotated[str | None, Cookie(alias=JIUWENCLAW_ID_COOKIE)] = None,
):
    """查询当前用户在本实例的 Skill 上传资格与最近申请状态。

    实例标识优先取 query（前端上下文）；缺省时取用户面动态反代 Cookie。
    ``allowed`` 只由授权行推导，不回落审批单状态。
    """
    if not settings.skill_upload_approval_enabled:
        raise HTTPException(status_code=403, detail=SKILL_UPLOAD_FEATURE_DISABLED)
    jid = str(jiuwenclaw_id or active_cluster or "").strip()
    if not jid:
        raise HTTPException(status_code=400, detail="jiuwenclaw_id is required")
    uid = _user_id(user)
    groups = list(getattr(user, "groups", []) or [])
    admitted = await UserConsoleService(handler).user_can_access_instance(uid, jid, groups)
    if not admitted:
        raise HTTPException(status_code=403, detail="instance not admitted")
    permission, latest = await resolve_upload_permission(
        handler, cluster_id=jid, user_id=uid, groups=groups
    )
    data: dict[str, Any] = {
        "jiuwenclaw_id": jid,
        "allowed": bool(permission.get("allowed")),
        "source": permission.get("source") or "none",
        "application_status": None,
        "order_num": None,
        "reject_comment": None,
        "checked_at": permission.get("checked_at"),
    }
    if latest is not None:
        data["application_status"] = latest.get("status")
        data["order_num"] = latest.get("order_num")
        data["reject_comment"] = latest.get("reject_comment") or None
    return _ok(data)


@user_console_router.post(
    "/skills/upload-permission-requests",
    response_model=ResponseModel,
)
async def submit_skill_upload_permission_request(
    body: SkillUploadPermissionSubmitBody,
    handler: _Handler,
    user: _CurUser,
    authorization: Annotated[str | None, Header()] = None,
):
    """提交上传资格申请：校验上下文准入后建单，待管理面审批。"""
    if not settings.skill_upload_approval_enabled:
        raise HTTPException(status_code=403, detail=SKILL_UPLOAD_FEATURE_DISABLED)
    uid = _user_id(user)
    admitted = await UserConsoleService(handler).user_can_access_context(
        uid,
        jiuwenclaw_id=body.jiuwenclaw_id,
        group_id=body.group_id,
        bot_id=body.bot_id,
        groups=list(getattr(user, "groups", []) or []),
        authorization=authorization,
    )
    if not admitted:
        raise HTTPException(status_code=403, detail="context not admitted")
    try:
        result = await ApprovalService(handler).submit_skill_upload_permission(
            applicant_id=uid,
            group_id=body.group_id,
            bot_id=body.bot_id,
            cluster_id=body.jiuwenclaw_id,
            reason=body.reason,
            approver_id=body.approver_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok({**result, "business_type": SKILL_UPLOAD_PERMISSION})


@user_console_router.get(
    "/approvals/candidates",
    response_model=ResponseModel,
)
async def list_approval_candidates(
    handler: _Handler,
    user: _CurUser,
):
    """扩容申请可选审批人：持有 approval:act 且非当前用户。"""
    items = await ApprovalService(handler).list_approver_candidates(
        exclude_user_id=_user_id(user),
    )
    return _ok({"items": items})


@user_console_router.get(
    "/approvals/mine",
    response_model=ResponseModel,
)
async def list_my_approvals(
    handler: _Handler,
    user: _CurUser,
    business_type: Annotated[str | None, Query(max_length=64)] = None,
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = None,
    search: Annotated[str | None, Query(max_length=128)] = None,
):
    """申请人侧「我的申请」：通用审批列表，可按业务类型/状态/关键字过滤。

    ``search`` 匹配单号、标题、申请说明。
    """
    try:
        items = await ApprovalService(handler).list_mine(
            applicant_id=_user_id(user),
            business_type=business_type,
            status=status,
            search=search,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok({"items": items})


@user_console_router.post(
    "/approvals/{order_num}/cancel",
    response_model=ResponseModel,
)
async def cancel_my_approval(
    order_num: str,
    handler: _Handler,
    user: _CurUser,
):
    """申请人撤回本人 pending 申请（任意 business_type）。"""
    try:
        result = await ApprovalService(handler).cancel(
            order_num=order_num,
            operator_id=_user_id(user),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="approval order not found")
    return _ok(result)
