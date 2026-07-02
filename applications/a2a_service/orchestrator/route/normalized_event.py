from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class NormalizedEvent(BaseModel):
    type: str = Field(description="事件类型")
    data: Dict[str, Any] = Field(default_factory=dict, description="事件数据")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据（含 source、frame_type 等）")


class RouteContext(BaseModel):
    task_id: str = Field(default="", description="当前任务 ID")
    current_task: Optional[Any] = Field(default=None, description="当前 Task 对象")
    conv_id: str = Field(default="", description="会话 ID（conversation_id）")
    root_task_id: str = Field(default="", description="根任务 ID（首轮由 api.dispatch 通过 conv_id→task_id 映射查得）")
    agent_key: str = Field(default="", description="当前节点 Agent 名称（即 source_agent）")
    is_specify_task: bool = Field(default=True, description="是否指定了 task_id（False 表示老版本未传入 task_id，走兼容逻辑）")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RouteTarget(BaseModel):
    type: str = Field(description="路由目标类型: local_agent / remote_agent / channel")
    agent_key: str = Field(default="", description="目标 Agent 标识（type=remote_agent 时使用）")
