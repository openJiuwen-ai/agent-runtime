# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""枚举定义"""

from enum import Enum


class DeploymentType(str, Enum):
    """部署类型"""
    AGENT = "agent"
    PLUGIN = "plugin"
    IMAGE = "image"


class DeploymentStatus(str, Enum):
    """部署状态"""
    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    RUNNING_NOTREADY = "running_not_ready"


class DeployMode(str, Enum):
    """部署模式"""
    SUBPROCESS = "subprocess"
    DOCKER = "docker"
    K8S = "k8s"
