"""Skill 上传资格模块请求体。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillUploadPermissionSubmitBody(BaseModel):
    """用户面提交上传资格申请；审批人选择与配额扩容申请一致。"""

    jiuwenclaw_id: str = Field(..., min_length=1, max_length=64)
    group_id: str = Field(default="", max_length=64)
    bot_id: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=1024)
    approver_id: str = Field(..., min_length=1, max_length=64)
