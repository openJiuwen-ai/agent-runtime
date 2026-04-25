# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务实例部署抽象：K8s / Docker / 无部署（空跑）可插拔。"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class IDeployController(Protocol):
    """一个运行实例的部署后端（K8s Pod 或 Docker 容器等）。"""

    @property
    def resource_id(self) -> Optional[str]:
        """部署后资源名（如 pod 名/容器名），未部署则为 None。"""
        ...

    async def deploy(self) -> Any:
        """执行部署，返回环境相关的描述对象（如 PodDeployInfo 或 DockerRunInfo）。"""
        ...

    async def delete(self) -> str:
        """缩容/删除资源，返回被删除的 resource_id。"""
        ...


class NoOpDeployController:
    """不部署，仅调试用。"""

    @property
    def resource_id(self) -> None:
        return None

    async def deploy(self) -> None:
        return None

    async def delete(self) -> str:
        return ""
