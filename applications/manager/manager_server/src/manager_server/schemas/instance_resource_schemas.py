"""实例资源模块请求体：instance_agent_resource / instance_service_resource。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field

from manager_server.infrastructure.match_expr import validate_match_expr

MatchExprField = Annotated[Any, BeforeValidator(validate_match_expr)]


class CreateInstanceAgentResourceBody(BaseModel):
    """在实例上新增 Agent 资源（生成 resource_id）。"""

    ref_template_id: str = Field(..., min_length=1, max_length=100)
    match_exprs: list[MatchExprField] = Field(..., min_length=1)
    resource_name: str = Field(..., min_length=1, max_length=128)
    resource_desc: str | None = Field(default=None, max_length=512)
    enabled: bool = True
    expires_at: datetime | None = None
    data: dict[str, Any] | None = None


class UpdateInstanceAgentResourceBody(BaseModel):
    """按 resource_id 整份覆盖 Agent 资源。"""

    match_exprs: list[MatchExprField] = Field(..., min_length=1)
    resource_name: str = Field(..., min_length=1, max_length=128)
    resource_desc: str | None = Field(default=None, max_length=512)
    enabled: bool = True
    expires_at: datetime | None = None
    data: dict[str, Any] | None = None


class ListInstanceAgentResourcesQuery(BaseModel):
    """实例 Agent 资源列表查询参数（后端排序/筛选）。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    search: str | None = Field(default=None, max_length=256)
    enabled: bool | None = None
    sort_by: str | None = Field(
        default=None,
        description="排序字段：resource_id、template_name、granted_by、expires_at、enabled、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class CreateInstanceServiceResourceBody(BaseModel):
    """在实例上新增服务资源（生成 resource_id）。"""

    ref_template_id: str = Field(..., min_length=1, max_length=100)
    match_exprs: list[MatchExprField] = Field(..., min_length=1)
    resource_name: str = Field(..., min_length=1, max_length=128)
    resource_desc: str | None = Field(default=None, max_length=512)
    priority: int = 0
    enabled: bool = True
    expires_at: datetime | None = None
    data: dict[str, Any] | None = None


class UpdateInstanceServiceResourceBody(BaseModel):
    """按 resource_id 整份覆盖服务资源。"""

    match_exprs: list[MatchExprField] = Field(..., min_length=1)
    resource_name: str = Field(..., min_length=1, max_length=128)
    resource_desc: str | None = Field(default=None, max_length=512)
    priority: int = 0
    enabled: bool = True
    expires_at: datetime | None = None
    data: dict[str, Any] | None = None


class ListInstanceServiceResourcesQuery(BaseModel):
    """实例服务资源列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    search: str | None = Field(default=None, max_length=256)
    enabled: bool | None = None
    sort_by: str | None = Field(
        default=None,
        description="排序字段：resource_id、resource_name、template_name、priority、granted_by、expires_at、enabled、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")
