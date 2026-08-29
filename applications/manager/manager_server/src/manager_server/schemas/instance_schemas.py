"""实例管理 API 请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


def _norm_host(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().rstrip("/")
    return text or None


class CreateInstanceBody(BaseModel):
    jiuwenclaw_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4096)
    namespace: str = Field(default="default", max_length=64)
    space_id: str = Field(default="default", max_length=64)
    created_by: str = Field(default="system", max_length=64)
    gateway_config_host: str = Field(..., min_length=1, max_length=512)
    runtime_config_host: str = Field(..., min_length=1, max_length=512)
    data: dict[str, Any] | None = None


class InstanceUpdateBody(BaseModel):
    """更新 instance_info（未传字段不修改）。

    Gateway/Runtime 的 status 与 last_alive 由 Manager 探活维护，不可通过本接口修改。
    """

    jiuwenclaw_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=4096)
    namespace: str | None = Field(default=None, max_length=64)
    space_id: str | None = Field(default=None, max_length=64)
    gateway_config_host: str | None = Field(default=None, max_length=512)
    runtime_config_host: str | None = Field(default=None, max_length=512)
    data: dict[str, Any] | None = None
    updated_by: str | None = Field(default=None, max_length=64)


class InstanceSummary(BaseModel):
    jiuwenclaw_id: str
    jiuwenclaw_name: str
    namespace: str
    space_id: str
    gateway_config_host: str
    gateway_status: str
    gateway_last_alive: str | None = None
    runtime_config_host: str
    runtime_status: str
    runtime_last_alive: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class InstanceListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    gateway_status: str | None = None
    runtime_status: str | None = None
    search: str | None = Field(
        default=None,
        max_length=256,
        description="按实例名称、实例 ID、命名空间、Gateway/Runtime 状态模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：jiuwenclaw_name、gateway_status、gateway_last_alive、"
            "runtime_status、runtime_last_alive、namespace、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class InstanceDetail(InstanceSummary):
    description: str | None
    data: dict[str, Any] | None
    created_by: str
    updated_by: str | None = None
