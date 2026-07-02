from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RequesterSourceProfile(BaseModel):
    suspended_states: List[str] = Field(
        default_factory=lambda: ["INPUT_REQUIRED", "AUTH_REQUIRED"],
        description="挂起状态列表（续轮判断条件）",
    )


class LocalAgentSourceProfile(BaseModel):
    delegate_types: List[str] = Field(
        default_factory=lambda: ["delegate", "sub_agent_dispatch", "multi_delegate"],
        description="委托事件类型集合（event.type 在此集合中 → 路由到 Remote Agent）",
    )
    default_remote_agent: str = Field(
        default="versatile_adapter",
        description="默认远程 Agent 标识（delegate 事件未指定 agent_key 时使用）",
    )


class RemoteAgentSourceProfile(BaseModel):
    terminal_frame_types: List[str] = Field(
        default_factory=lambda: ["CONTROL_COMPLETED", "CONTROL_FAILED"],
        description="终态帧类型集合（frame_type 在此集合中 → 路由到 Local Agent）",
    )
    frame_type_map: Dict[str, str] = Field(
        default_factory=lambda: {
            "COMPLETED": "CONTROL_COMPLETED",
            "FAILED": "CONTROL_FAILED",
            "INPUT_REQUIRED": "CONTROL_INPUT_REQUIRED",
            "AUTH_REQUIRED": "CONTROL_AUTH_REQUIRED",
            "SUBMITTED": "CONTROL_SUBMITTED",
            "WORKING": "CONTROL_WORKING",
            "CANCELED": "CONTROL_CANCELED",
            "REJECTED": "CONTROL_REJECTED",
            "ARTIFACT": "DATA",
        },
        description="A2A 事件状态 → frame_type 的映射表",
    )
    default_frame_type: str = Field(
        default="CONTROL_UNSPECIFIED",
        description="默认帧类型（映射表中未匹配时使用）",
    )


class SourceRouteProfile(BaseModel):
    requester: RequesterSourceProfile = Field(default_factory=RequesterSourceProfile)
    local_agent: LocalAgentSourceProfile = Field(default_factory=LocalAgentSourceProfile)
    remote_agent: RemoteAgentSourceProfile = Field(default_factory=RemoteAgentSourceProfile)


class RouteConfig(BaseModel):
    handlers: Dict[str, str] = Field(
        default_factory=dict,
        description="处理器插件映射（目标类型 → 处理器类路径）",
    )
    profiles: Dict[str, SourceRouteProfile] = Field(
        default_factory=dict,
        description="路由策略配置（Agent 名称 → SourceRouteProfile），支持多 Agent 独立配置",
    )
    default_profile: SourceRouteProfile = Field(
        default_factory=SourceRouteProfile,
        description="默认路由策略配置（未匹配到 Agent 专用配置时使用）",
    )
    max_cascade_depth: int = Field(
        default=10,
        description="级联查找最大深度限制，防止循环嵌套导致无限递归（默认10）",
    )


class RouteConfigLoader:
    """路由配置加载器：从 YAML 文件加载配置，支持多 Agent 配置合并"""

    @staticmethod
    def load(config_path: str) -> RouteConfig:
        path = Path(config_path)
        if not path.exists():
            return RouteConfig()

        import yaml

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        return RouteConfig(**raw)

    @staticmethod
    def resolve_profile(config: RouteConfig, agent_key: str) -> SourceRouteProfile:
        return config.profiles.get(agent_key, config.default_profile)
