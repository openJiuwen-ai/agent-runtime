"""角色与权限 API 请求模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PermissionListQuery(BaseModel):
    enabled: bool | None = None
    scope: Literal["admin", "org", "user"] | None = None
    search: str | None = Field(
        default=None,
        max_length=256,
        description="搜索权限 ID、名称、描述",
    )


class RoleListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    scope: Literal["admin", "org", "user"] | None = None
    search: str | None = Field(
        default=None,
        max_length=256,
        description="搜索角色 ID、名称、描述",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：name、description、updated_at、role_id",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class RoleCreateBody(BaseModel):
    role_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="角色 ID：英文字母、数字、下划线、连字符，最长 64",
    )
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    scope: Literal["admin", "org", "user"] = "admin"
    permission_ids: list[str] = Field(default_factory=list, max_length=128)


class RoleUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    scope: Literal["admin", "org", "user"] | None = None
    enabled: bool | None = None
    permission_ids: list[str] | None = Field(default=None, max_length=128)


class RoleUsersBody(BaseModel):
    user_ids: list[str] = Field(default_factory=list, max_length=10_000)


class RoleUserAssignBody(BaseModel):
    user_ids: list[str] = Field(..., min_length=1, max_length=1000)
    expires_at: datetime | None = Field(
        default=None,
        description="过期时间；空表示长期有效",
    )


class RoleUserListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    search: str | None = Field(
        default=None,
        max_length=256,
        description="搜索用户 ID、授权人",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：user_id、granted_by、expires_at、created_at、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")
