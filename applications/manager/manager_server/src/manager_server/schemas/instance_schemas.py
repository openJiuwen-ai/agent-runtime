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
    gateway_host: str = Field(..., min_length=1, max_length=512)
    runtime_host: str = Field(..., min_length=1, max_length=512)
    user_web_host: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="写入 instance_info.user_web_host",
    )
    gateway_web_http_host: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="写入 instance_info.data.gateway_web_http_host",
    )
    gateway_web_ws_host: str | None = Field(
        default=None,
        max_length=512,
        description="写入 instance_info.data.gateway_web_ws_host",
    )
    data: dict[str, Any] | None = None


class InstanceUpdateBody(BaseModel):
    """更新 instance_info（未传字段不修改）。

    Gateway / Runtime / User Web 的 status 与 last_alive 由 Manager 探活维护，
    不可通过本接口修改。gateway_web_*_host 仍写入 ``data`` JSON。
    """

    jiuwenclaw_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=4096)
    namespace: str | None = Field(default=None, max_length=64)
    space_id: str | None = Field(default=None, max_length=64)
    gateway_host: str | None = Field(default=None, max_length=512)
    runtime_host: str | None = Field(default=None, max_length=512)
    user_web_host: str | None = Field(
        default=None,
        max_length=512,
        description="写入 instance_info.user_web_host",
    )
    gateway_web_http_host: str | None = Field(
        default=None,
        max_length=512,
        description="写入 instance_info.data.gateway_web_http_host",
    )
    gateway_web_ws_host: str | None = Field(
        default=None,
        max_length=512,
        description="写入 instance_info.data.gateway_web_ws_host",
    )
    data: dict[str, Any] | None = None
    updated_by: str | None = Field(default=None, max_length=64)


class InstanceSummary(BaseModel):
    jiuwenclaw_id: str
    jiuwenclaw_name: str
    namespace: str
    space_id: str
    gateway_host: str
    gateway_status: str
    gateway_last_alive: str | None = None
    runtime_host: str
    runtime_status: str
    runtime_last_alive: str | None = None
    user_web_host: str | None = None
    user_web_status: str = Field(
        default="pending",
        description="instance_info.user_web_status",
    )
    user_web_last_alive: str | None = Field(
        default=None,
        description="instance_info.user_web_last_alive",
    )
    # 以下两项来自 data JSON，响应中展开便于前端展示
    gateway_web_http_host: str | None = None
    gateway_web_ws_host: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class InstanceListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    gateway_status: str | None = None
    runtime_status: str | None = None
    user_web_status: str | None = Field(
        default=None,
        description="按 instance_info.user_web_status 过滤",
    )
    search: str | None = Field(
        default=None,
        max_length=256,
        description="按实例名称、实例 ID、命名空间、Gateway/Runtime/User Web 状态模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：jiuwenclaw_name、gateway_status、gateway_last_alive、"
            "runtime_status、runtime_last_alive、user_web_status、user_web_last_alive、"
            "namespace、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class InstanceDetail(InstanceSummary):
    description: str | None
    data: dict[str, Any] | None
    created_by: str
    updated_by: str | None = None
