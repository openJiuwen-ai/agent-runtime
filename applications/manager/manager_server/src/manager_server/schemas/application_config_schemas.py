"""应用配置 API 请求/响应：日志脱敏规则。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LogMaskingRuleCreateBody(BaseModel):
    rule_name: str = Field(..., max_length=128)
    description: str | None = Field(default=None, max_length=512)
    pattern: str = Field(..., min_length=1)
    replacement: str | None = Field(default=None, max_length=64)
    priority: int = 0
    with_fingerprint: bool = False
    enabled: bool = True
    data: dict[str, Any] | None = None


class LogMaskingRuleUpdateBody(BaseModel):
    rule_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    pattern: str | None = None
    replacement: str | None = Field(default=None, max_length=64)
    priority: int | None = None
    with_fingerprint: bool | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class LogMaskingRuleListQuery(BaseModel):
    enabled: bool | None = None
    source: str | None = Field(
        default=None,
        description="按来源筛选：builtin、custom",
    )
    search: str | None = Field(
        default=None,
        description="搜索规则 ID、名称、描述、匹配正则表达式、替换文本、优先级、来源",
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：rule_name、description、pattern、replacement、priority、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class LogMaskingRuleOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    rule_id: str
    rule_name: str
    description: str | None
    pattern: str
    replacement: str
    priority: int
    with_fingerprint: bool = False
    source: str
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class WorkspaceQuotaPolicyListQuery(BaseModel):
    policy_id: str | None = None
    enabled: bool | None = None
    search: str | None = Field(
        default=None,
        max_length=256,
        description="搜索策略 ID、名称、描述、匹配范围、来源单号、优先级、配额与阈值",
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：policy_name、policy_desc、priority、match_expr、limit_bytes、"
            "soft_percent、hard_percent、source_order_num、updated_at。缺省按 priority 升序"
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
    soft_percent: int = 80
    hard_percent: int = 100
    source_order_num: str | None = Field(default=None, max_length=64)


class WorkspaceQuotaPolicyPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: str | None = Field(default=None, min_length=1, max_length=128)
    policy_desc: str | None = Field(default=None, max_length=512)
    limit_bytes: int | None = None
    match_expr: Any = None
    priority: int | None = None
    soft_percent: int | None = None
    hard_percent: int | None = None
    enabled: bool | None = None
