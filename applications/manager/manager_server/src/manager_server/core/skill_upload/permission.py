"""Skill 上传资格：由 instance_grant 推导，并与最近一张申请单组合。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.approval import ApprovalService
from manager_server.core.approval.service import SKILL_UPLOAD_PERMISSION
from manager_server.core.instance_access import InstanceGrantService
from manager_server.infrastructure.utils import iso_datetime, utc_now


async def resolve_upload_permission(
    handler: DBHandler,
    *,
    cluster_id: str,
    user_id: str,
    groups: list[str] | tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """返回 ``(permission, latest_order)``。

    ``permission`` 只由授权行推导（``allowed`` / ``source``），不回落单据状态；
    ``latest_order`` 仅在未授权时用于给出 ``pending`` / ``rejected`` 与驳回意见。
    """
    permission = await InstanceGrantService(handler).effective_upload_policy(
        cluster_id, user_id, groups
    )
    latest_order = None
    if not permission.get("allowed"):
        latest_order = await ApprovalService(handler).latest_open_order(
            applicant_id=user_id,
            cluster_id=cluster_id,
            business_type=SKILL_UPLOAD_PERMISSION,
        )
    return {
        **permission,
        "checked_at": iso_datetime(utc_now()),
    }, latest_order
