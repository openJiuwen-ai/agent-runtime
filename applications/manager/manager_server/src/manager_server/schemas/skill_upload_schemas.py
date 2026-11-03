"""Skill 上传授权与用户申请 API 请求模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SubjectType = Literal["user", "org"]
PermissionSource = Literal["admin", "approval"]


class SkillUploadGrantListQuery(BaseModel):
    cluster_id: str | None = Field(default=None, description="按企业实例过滤；管理员可省略查全部")
    subject_type: SubjectType | None = None
    source: PermissionSource | None = None
    enabled: bool | None = Field(default=None, description="按启用状态过滤：true 仅启用的，false 仅禁用的")
    search: str | None = Field(
        default=None,
        max_length=128,
        description="搜索主体 ID、授权人、来源单号",
    )
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)


class SkillUploadGrantBindBody(BaseModel):
    """管理员把一批用户/组织加入上传授权。"""

    cluster_id: str = Field(..., min_length=1, max_length=64, description="企业实例 ID")
    subject_type: SubjectType
    ids: list[str] = Field(..., min_length=1, max_length=1000)
    expires_at: datetime | None = Field(default=None, description="空表示长期有效")
    enabled: bool = True


class SkillUploadGrantUpdateBody(BaseModel):
    enabled: bool | None = None
    expires_at: datetime | None = None
    # 显式清空过期时间（与 expires_at=null 在 JSON 中难以区分时使用）
    clear_expires_at: bool = False


class SkillUploadGrantRevokeBody(BaseModel):
    cluster_id: str = Field(..., min_length=1, max_length=64, description="企业实例 ID")
    subject_type: SubjectType
    ids: list[str] = Field(..., min_length=1, max_length=1000)


class UserConsoleSkillUploadSubmitBody(BaseModel):
    """用户面提交 Skill 上传资格申请；上下文字段来自真实用户会话。

    ``context_id`` 为当前可信上下文引用（企业实例 id），后端会做会话准入口径
    校验；``group_id``/``bot_id`` 为准入校验所需的三元组扩展，不接受占位值。
    """

    context_id: str = Field(..., min_length=1, max_length=64, description="当前可信上下文引用")
    group_id: str = Field(default="", max_length=64)
    bot_id: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=1024)
    approver_id: str = Field(..., min_length=1, max_length=64)
