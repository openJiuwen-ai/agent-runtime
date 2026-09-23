"""扩容审批 API 请求模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ApprovalListQuery(BaseModel):
    """管理面审批列表查询参数。"""

    business_type: str | None = Field(default=None, max_length=64)
    view: Literal["todo", "mine"] | None = None
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = None
    group_id: str | None = Field(default=None, max_length=64)
    search: str | None = Field(
        default=None,
        max_length=128,
        description="匹配单号、标题、申请说明",
    )


class ApprovalActionBody(BaseModel):
    action: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1024)


class UserConsoleExpandSubmitBody(BaseModel):
    """用户面扩容申请：配额快照由 Manager 服务端解析。"""

    jiuwenclaw_id: str = Field(..., min_length=1, max_length=64)
    group_id: str = Field(default="", max_length=64)
    bot_id: str = Field(..., min_length=1, max_length=64)
    requested_limit_bytes: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=1024)
    used_bytes: int | None = Field(default=None, ge=0)
    # 指定审批人：须持有 approval:act，且不能是申请人本人
    approver_id: str = Field(..., min_length=1, max_length=64)
