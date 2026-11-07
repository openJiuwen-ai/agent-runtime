"""实例准入模块请求体：instance_grant 绑定 / 更新。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LoginPolicy = Literal["allow", "deny"]


class InstanceBindBody(BaseModel):
    """把一批用户/组织绑定到某实例（jiuwenclaw_id 走路径）。"""

    ids: list[str] = Field(..., min_length=1, max_length=1000)
    login_policy: LoginPolicy = "allow"
    expires_at: datetime | None = None
    enabled: bool = True


class InstanceUnbindBody(BaseModel):
    """从某实例解绑一批用户/组织。"""

    ids: list[str] = Field(..., min_length=1, max_length=1000)


class InstanceGrantUpdateBody(BaseModel):
    """更新单条 instance_grant（启用 / 登录权限 / 过期时间）。"""

    enabled: bool | None = None
    login_policy: LoginPolicy | None = None
    expires_at: datetime | None = None
    # 显式清空过期时间（与 expires_at=null 在 JSON 中难以区分时使用）
    clear_expires_at: bool = False
