"""企业版 Skill 按包上传审批 API 请求模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillUploadSubmitBody(BaseModel):
    """用户面提交：描述符必须原样转发 AgentServer 的产物。"""

    jiuwenclaw_id: str = Field(..., min_length=1, max_length=64)
    group_id: str = Field(default="", max_length=64)
    bot_id: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=1024)
    approver_id: str = Field(..., min_length=1, max_length=64)
    replace: bool = False
    package_info: dict[str, Any]


class SkillUploadInstallResultBody(BaseModel):
    jiuwenclaw_id: str = Field(..., min_length=1, max_length=64)
    upload_id: str = Field(..., min_length=32, max_length=32)
    digest: str = Field(..., min_length=1, max_length=80)
    status: Literal["installed", "failed"]
    detail: str | None = Field(default=None, max_length=1024)
