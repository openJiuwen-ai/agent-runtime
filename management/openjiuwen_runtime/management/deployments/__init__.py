# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""部署模块

提供三种部署模式:
- subprocess: 本地进程部署
- docker: Docker容器部署
- k8s: Kubernetes部署
"""

from .base import (
    CommonParams,
    DeployContext,
    DeployResult,
    Deployer,
    BaseDeploymentStrategy,
)
from .subprocess import (
    SubprocessParams,
    ProcessInfo,
    ProcessCreate,
    PROCESS_TABLE_DEF,
    LocalSubprocessDeployer,
    SubprocessStrategy,
)
from .docker import (
    DockerParams,
    DockerInfo,
    DockerCreate,
    DOCKER_TABLE_DEF,
    DockerDeployer,
    DockerStrategy,
)

K8S_IMPORT_ERROR = None

try:
    from .k8s import (
        K8sParams,
        K8sInfo,
        K8sCreate,
        K8S_TABLE_DEF,
        K8sDeployer,
        K8sStrategy,
    )
except Exception as exc:  # pragma: no cover - depends on local runtime availability
    K8S_IMPORT_ERROR = exc
    K8sParams = None
    K8sInfo = None
    K8sCreate = None
    K8S_TABLE_DEF = None
    K8sDeployer = None
    K8sStrategy = None

__all__ = [
    # Base
    "CommonParams",
    "DeployContext",
    "DeployResult",
    "Deployer",
    "BaseDeploymentStrategy",
    # Subprocess
    "SubprocessParams",
    "ProcessInfo",
    "ProcessCreate",
    "PROCESS_TABLE_DEF",
    "LocalSubprocessDeployer",
    "SubprocessStrategy",
    # Docker
    "DockerParams",
    "DockerInfo",
    "DockerCreate",
    "DOCKER_TABLE_DEF",
    "DockerDeployer",
    "DockerStrategy",
    # K8s
    "K8S_IMPORT_ERROR",
    "K8sParams",
    "K8sInfo",
    "K8sCreate",
    "K8S_TABLE_DEF",
    "K8sDeployer",
    "K8sStrategy",
]
