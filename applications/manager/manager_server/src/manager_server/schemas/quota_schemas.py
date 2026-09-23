"""配额 API 请求/响应 Schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceQuotaEffectiveQuery(BaseModel):
    """查询用户在指定 group/bot 下的生效配额。"""

    user_id: str = Field(..., min_length=1)
    group_id: str = Field(..., min_length=1)
    bot_id: str = Field(..., min_length=1)


class WorkspaceQuotaPolicyListQuery(BaseModel):
    policy_id: str | None = None
    enabled: bool | None = None
    search: str | None = Field(
        default=None,
        max_length=256,
        description="搜索策略 ID、名称、描述、匹配范围、来源、来源单号、优先级、配额与阈值",
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：policy_name、policy_desc、priority、match_expr、limit_bytes、"
            "soft_percent、hard_percent、source、source_order_num、updated_at。缺省按 priority 升序"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)


class WorkspaceQuotaPolicyCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(..., min_length=1, max_length=128)
    policy_desc: str | None = Field(default=None, max_length=512)
    match_expr: Any = None
    priority: int = 0
    limit_bytes: int
    soft_percent: int = Field(ge=0, le=100, default=80)
    hard_percent: int = Field(ge=0, le=100, default=100)
    source_order_num: str | None = Field(default=None, max_length=64)


class WorkspaceQuotaPolicyPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: str | None = Field(default=None, min_length=1, max_length=128)
    policy_desc: str | None = Field(default=None, max_length=512)
    limit_bytes: int | None = None
    match_expr: Any = None
    priority: int | None = None
    soft_percent: int | None = Field(default=None, ge=0, le=100)
    hard_percent: int | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None
