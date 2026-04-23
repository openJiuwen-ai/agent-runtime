# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""部署相关请求参数（收敛 Manager 方法形参数量）"""

from dataclasses import dataclass, field
from typing import Any, Optional

from .enums import DeployMode, DeploymentStatus, DeploymentType


@dataclass
class ListDeploymentsParams:
    """列出部署的过滤与分页条件"""

    deployment_type: Optional[DeploymentType] = None
    deployment_status: Optional[DeploymentStatus] = None
    user_id: Optional[str] = None
    space_id: Optional[str] = None
    limit: int = 100
    offset: int = 0


@dataclass
class DeployAgentParams:
    """部署 Agent 的参数（策略扩展字段放入 extras）"""

    name: str
    version: str
    mode: DeployMode = DeployMode.SUBPROCESS
    user_id: Optional[str] = None
    space_id: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeployPluginParams:
    """部署 Plugin 的参数（策略扩展字段放入 extras）"""

    name: str
    version: str
    mode: DeployMode = DeployMode.SUBPROCESS
    url: Optional[str] = None
    user_id: Optional[str] = None
    space_id: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeployImageParams:
    """部署镜像的参数（策略扩展字段放入 extras，如环境变量、端口等配置）"""

    image: str
    name: str
    version: str
    mode: DeployMode
    user_id: Optional[str] = None
    space_id: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)
