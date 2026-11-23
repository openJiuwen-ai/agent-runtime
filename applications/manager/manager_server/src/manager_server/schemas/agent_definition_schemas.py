"""Agent 模板 API 请求模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentDefinitionListQuery(BaseModel):
    """Agent 模板列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    search: str | None = Field(
        default=None,
        max_length=256,
        description="按 Agent Name、Agent ID 模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：agent_name、agent_id、created_at、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class AgentDefinitionCreateBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    agent_config_template_id: str = Field(..., min_length=1, max_length=100)
    runtime_template_id: str = Field(..., min_length=1, max_length=100)
    agent_name: str = Field(..., min_length=1, max_length=128)


class AgentDefinitionRenameBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    agent_name: str = Field(..., min_length=1, max_length=128)
