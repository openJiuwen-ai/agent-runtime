# coding: utf-8
"""Session Manager 数据模型（template / scope 配置 / pod_spec 派生）。

template 字段定义见 HLD §3.1「数据结构定义」。DB 列名沿用 EE 兼容名：
- scope_concurrency → DB ``session_concurrency``
- pod_concurrency   → DB ``service_concurrency``
- pod_ttl           → DB ``service_ttl``
- min_idle_pods     → DB ``min_idle_services``
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..spec_fields import DEPLOY_VER_FIELDS, POLICY_FIELDS  # noqa: F401 - 字段分类定义
from ..util import fingerprint


@dataclass(frozen=True)
class Template:
    """一个 service_config_template 行的业务视图（HLD template 结构）。"""

    template_id: str
    # 策略参数（B 类）
    scope_concurrency: int = 3
    pod_concurrency: int = 2
    session_ttl: int = 60
    pod_ttl: int = 300
    min_idle_pods: int = 0
    # deploy 子集（A 类）
    agent_image: str = ""
    namespace: str = "default"
    pod_name: str = "agentserver"          # Pod 名前缀（pod_id = 前缀-随机后缀）
    container_name: str = "agent"
    container_port: int = 8080
    sse_port: int = 8080                   # gateway 直连 Pod 的 SSE 端口
    sse_path: str = "/sse"
    image_pull_policy: str = "IfNotPresent"
    readiness_initial_delay: int = 5
    readiness_period: int = 5
    ready_timeout: int = 300               # deploy 等 Ready 的超时（秒）
    ready_poll_interval: int = 2
    nfs_server: str | None = None
    nfs_path: str | None = None
    nfs_mount_path: str | None = None
    agent_cpu_request: str | None = None
    agent_memory_request: str | None = None
    agent_cpu_limit: str | None = None
    agent_memory_limit: str | None = None
    # deploy 凭证（B 类例外：只影响新 deploy，不日落）
    kubeconfig: str | None = None
    # 元信息
    template_name: str = ""
    description: str = ""
    enabled: bool = True
    message_timeout: int = 600
    data: dict[str, Any] = field(default_factory=dict)

    # -------------------------------------------------------------- 派生

    @property
    def max_pods(self) -> int:
        """max_pods = ⌈scope_concurrency / pod_concurrency⌉（派生值，不入 template）。"""
        pc = max(self.pod_concurrency, 1)
        return max(1, math.ceil(max(self.scope_concurrency, 0) / pc))

    def deploy_subset(self) -> dict[str, Any]:
        """acquire 下发 RM 的 pod_spec（deploy 子集 + kubeconfig + ready 参数）。"""
        out: dict[str, Any] = {name: getattr(self, name) for name in DEPLOY_VER_FIELDS}
        out["kubeconfig"] = self.kubeconfig
        return out

    def deploy_ver(self) -> str:
        """A 类字段 hash 指纹（不含 kubeconfig）。新旧不等即 A 类变更（场景 M）。"""
        return fingerprint({name: getattr(self, name) for name in DEPLOY_VER_FIELDS})

    def pool_config(self) -> dict[str, Any]:
        """acquire/update_pool_config 下发 RM 的池参数。

        pod_concurrency 供 RM 的 deploy follower 等待室推导上限（pc-1）——
        不参与 max_pods 判定（per-Pod 容量闸门仍在 SM 侧，红线不变）。
        """
        return {
            "min_idle_pods": self.min_idle_pods,
            "max_pods": self.max_pods,
            "pod_ttl": self.pod_ttl,
            "pod_concurrency": self.pod_concurrency,
        }


@dataclass(frozen=True)
class ScopeConfig:
    """resolve 的产物：template 业务参数（写进 ``scope:{scope_id}:config`` 缓存）。

    Redis HASH 字段与 dataclass 字段一一对应（int 字段以 str 存储）。
    """

    scope_id: str
    template_id: str
    scope_concurrency: int
    pod_concurrency: int
    session_ttl: int
    pod_ttl: int
    min_idle_pods: int
    max_pods: int
    deploy_ver: str
    ver: str = ""          # template 更新时间戳（观测用，不参与逻辑）

    def to_hash(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.__dict__.items()}

    @classmethod
    def from_hash(cls, scope_id: str, h: dict[str, str]) -> "ScopeConfig":
        return cls(
            scope_id=scope_id,
            template_id=h.get("template_id", ""),
            scope_concurrency=int(h.get("scope_concurrency", 0)),
            pod_concurrency=int(h.get("pod_concurrency", 1)),
            session_ttl=int(h.get("session_ttl", 60)),
            pod_ttl=int(h.get("pod_ttl", 300)),
            min_idle_pods=int(h.get("min_idle_pods", 0)),
            max_pods=int(h.get("max_pods", 1)),
            deploy_ver=h.get("deploy_ver", ""),
            ver=h.get("ver", ""),
        )
