# coding: utf-8
"""Resource Manager 数据模型：Pod 部署/状态视图 + 判死枚举。"""

from __future__ import annotations

from dataclasses import dataclass

# 判死状态枚举（沿用老 SDK FAILED_POD_STATUSES，真实踩过的清单）。
# Pending 不判死——deploy 路径靠 ready_timeout 兜，池内 Pod 卡 Pending 由轮询跟进。
DEAD_POD_STATUSES = frozenset({
    "Terminating",          # 删除中 / node 驱逐
    "Failed",
    "CrashLoopBackOff",     # 含 OOM 反复重启
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
})

# Pod label（与老 SDK 一致；cleanup 默认 selector）
POD_LABEL_KEY = "jiuwenclaw-component"
POD_LABEL_VALUE = "agentserver"
POD_LABEL_SELECTOR = f"{POD_LABEL_KEY}={POD_LABEL_VALUE}"


@dataclass(frozen=True)
class PodDeployInfo:
    """deploy 成功的物理信息（K8s 是物理态唯一真相源）。"""

    pod_id: str            # K8s 随机 Pod 名（严禁用业务 id 当实例 id）
    namespace: str
    pod_ip: str
    host_ip: str = ""
    node_name: str = ""


@dataclass(frozen=True)
class PodInfo:
    """get/list 返回的 Pod 状态视图（归一化 phase + 容器等待原因）。"""

    pod_id: str
    namespace: str
    phase: str             # 归一化状态（Running/Pending/Terminating/CrashLoopBackOff/...）
    ready: bool
    pod_ip: str = ""
    labels: dict[str, str] | None = None
    reason: str = ""       # 容器 waiting 原因（判死辅助/日志）
