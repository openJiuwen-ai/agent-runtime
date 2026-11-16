"""管理面启动部署 API 请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DeploymentCreateBody(BaseModel):
    action: Literal["up", "down"] = Field(
        default="up", description="up=部署/升级（幂等重跑）；down=卸载该命名空间产品面"
    )
    namespace: str = Field(..., min_length=1, max_length=63, description="目标命名空间")
    jiuwenclaw_name: str = Field(
        ..., min_length=1, max_length=128, description="部署成功后自动注册的实例名称"
    )
    deploy_mode: Literal["routing", "full"] = Field(
        default="full", description="AGENT_RUNTIME_DEPLOY_MODE"
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="默认参数覆盖（白名单键：镜像/副本/外置 DB/Redis/OBS 等）",
    )
    wait_timeout_secs: int | None = Field(
        default=None, ge=60, le=14400, description="Pod 就绪等待预算（透传 deploy.sh）"
    )


class DeploymentTaskSummary(BaseModel):
    deployment_id: str
    action: str = Field(default="up", description="up=部署；down=卸载")
    namespace: str
    jiuwenclaw_name: str
    deploy_mode: str
    status: str
    phase: str
    instance_jid: str | None = None
    error_summary: str | None = None
    created_by: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class DeploymentTaskDetail(DeploymentTaskSummary):
    # 组件时间线与阶段状态（defaults_snapshot 不外露，避免泄漏密码）
    progress: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    tool_version: str | None = None
    exit_code: int | None = None
    cancel_requested: bool = False


class DeploymentListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    status: str | None = None
    namespace: str | None = None
    search: str | None = Field(
        default=None, max_length=256, description="按任务 ID/命名空间/实例名/状态模糊搜索"
    )


class DeploymentLogItem(BaseModel):
    seq: int
    content: str
    created_at: str | None = None


class DeploymentLogsData(BaseModel):
    items: list[DeploymentLogItem]
    last_seq: int
    truncated: bool = False


class DeploymentDefaultsData(BaseModel):
    env: dict[str, str] = Field(description="默认参数（密码/密钥类已掩码）")
    tool_version: str
    deploy_enabled: bool
    available: bool
    unavailable_reason: str = ""


class DeploymentDefaultsUpdateBody(BaseModel):
    env: dict[str, str] = Field(description="要更新的默认参数键值（掩码值 ****** 忽略）")
