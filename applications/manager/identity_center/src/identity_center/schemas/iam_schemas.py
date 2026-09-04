"""身份目录管理请求体（组织 / 用户 / 成员）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from identity_center.infrastructure.utils import IDENTITY_ID_MAX_LENGTH, IDENTITY_ID_PATTERN_STR


class OrgListQuery(BaseModel):
    """组织列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)
    search: str | None = Field(default=None, max_length=256)
    status: str | None = Field(default=None, max_length=16)
    sort_by: str | None = Field(
        default=None,
        description="排序字段：group_id、display_name、status、created_at、updated_at",
    )
    sort_order: str | None = Field(
        default=None,
        pattern="^(asc|desc)$",
        description="排序方向：asc、desc",
    )


class UserListQuery(BaseModel):
    """用户列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)
    search: str | None = Field(default=None, max_length=256)
    status: str | None = Field(default=None, max_length=16)
    is_admin: bool | None = None
    sort_by: str | None = Field(
        default=None,
        description="排序字段：user_id、display_name、is_admin、status、created_at、updated_at",
    )
    sort_order: str | None = Field(
        default=None,
        pattern="^(asc|desc)$",
        description="排序方向：asc、desc",
    )


class OrgCreateBody(BaseModel):
    group_id: str | None = Field(
        default=None,
        max_length=IDENTITY_ID_MAX_LENGTH,
        pattern=IDENTITY_ID_PATTERN_STR,
    )
    display_name: str = Field(..., min_length=1, max_length=128)


class OrgUpdateBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=16)


class UserCreateBody(BaseModel):
    user_id: str | None = Field(
        default=None,
        max_length=IDENTITY_ID_MAX_LENGTH,
        pattern=IDENTITY_ID_PATTERN_STR,
    )
    display_name: str = Field(..., min_length=1, max_length=128)
    is_admin: bool = False
    # 未显式传 user_id 时 username 会作为 user_id，故与 user_id 同规则
    username: str = Field(
        ...,
        min_length=1,
        max_length=IDENTITY_ID_MAX_LENGTH,
        pattern=IDENTITY_ID_PATTERN_STR,
    )
    password: str = Field(..., min_length=1, max_length=256)


class UserUpdateBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    is_admin: bool | None = None
    status: str | None = Field(default=None, max_length=16)
    password: str | None = Field(default=None, max_length=256)


class SetMembershipBody(BaseModel):
    """整体覆盖某用户的组织绑定（批量）。"""

    group_ids: list[str] = Field(default_factory=list)


class AddMembersBody(BaseModel):
    """从组织侧批量加入用户（幂等）。"""

    user_ids: list[str] = Field(default_factory=list)


class UserBatchItem(BaseModel):
    """批量新建用户的单行（对应 Excel/CSV 一行）。

    字符集由 ``UserService.create`` 校验，便于按行返回错误而非整单 422。
    """

    username: str = Field(..., min_length=1, max_length=IDENTITY_ID_MAX_LENGTH)
    password: str = Field(..., min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=128)  # 空 → 回退 username
    is_admin: bool = False
    orgs: list[str] = Field(default_factory=list)  # 组织 id 或名称；无效自动忽略 → 无组织


class UsersBatchCreateBody(BaseModel):
    """批量新建用户请求体（前端解析 Excel/CSV 后提交 JSON）。"""

    users: list[UserBatchItem] = Field(..., min_length=1, max_length=500)
