# coding: utf-8
"""Session Manager 数据模型（template 业务视图 / pod_spec 派生）。

template 字段定义见 HLD §3.1「数据结构定义」。DB 列名与 wire 术语同名
(2026-09 起统一;曾用 EE 兼容名 session_concurrency/service_concurrency/
service_ttl/min_idle_services,存量库须 RENAME COLUMN)。

scope 定义(scope_id/index/引用模板/路由规则集)由 config_sync 全量下发,
见 ``routing.py``(RoutingScopeDef)与 ``routing_scope`` 表——不再由
(group_id, bot_id) 二元组派生。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..mounts import normalize_mounts
from ..sidecars import normalize_sidecars
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
    node_name: str | None = None
    run_as_user: int | None = None
    run_as_group: int | None = None
    pod_name: str = "agentserver"          # Pod 名前缀（pod_id = 前缀-随机后缀）
    container_name: str = "agent"
    container_port: int = 8080
    sse_port: int = 8080                   # gateway 直连 Pod 的 SSE 端口
    sse_path: str = "/sse"
    health_path: str = "/health"           # readiness 探针路径(真 AgentServer HTTP 入口为 /api/v1/health)
    agent_env: dict[str, str] = field(default_factory=dict)  # Agent 容器 env 注入(AGENT_HTTP_* 等)
    # envFrom 引用(K8s EnvFromSource 内部规范形:[{prefix?, secret_ref|config_map_ref:
    # {name, optional}}])。缺省 None 被 fingerprint 滤除 → 存量模板指纹零扰动;
    # 值变化 = 正确的 A 类日落(env 烘焙进 Pod)。仅新契约(config_sync containers
    # 形态)可下发;legacy 内联 payload 不接此字段。
    agent_env_from: list[dict[str, Any]] | None = None
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
    # 同 Pod sidecar 容器列表(A 类字段,通用机制,jiuwenbox 是第一个使用者;
    # 规范形与校验见 sidecars.py)。None 与 [] 统一归一为 None——fingerprint
    # 只滤 None,若以 [] 为默认会使全部存量模板 deploy_ver 变化 → 全量 A 类
    # 日落,不可接受(见 __post_init__ 的 normalize_sidecars)。
    sidecars: list[dict[str, Any]] | None = None
    # 主 agent 容器卷挂载(A 类;规范形与校验见 mounts.py,与 sidecar 挂载同款;
    # 空列表/坏值同样归一为 None 保指纹稳定)
    agent_host_path_mounts: list[dict[str, Any]] | None = None
    agent_configmap_mounts: list[dict[str, Any]] | None = None
    agent_pvc_mounts: list[dict[str, Any]] | None = None
    # deploy 凭证（B 类例外：只影响新 deploy，不日落）
    kubeconfig: str | None = None
    # 元信息
    template_name: str = ""
    description: str = ""
    enabled: bool = True
    message_timeout: int = 600
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 空列表/坏值 → None;逐项规范形 + 排序(指纹/DB/快照三处形态唯一)。
        # payload/DB 行/快照 JSON/测试手搓全部构造路径收敛于此。
        object.__setattr__(self, "sidecars", normalize_sidecars(self.sidecars))
        for field in ("agent_host_path_mounts", "agent_configmap_mounts",
                      "agent_pvc_mounts"):
            kind = field.replace("agent_", "", 1)
            object.__setattr__(self, field, normalize_mounts(getattr(self, field), kind))
        # 路径字段归一：缺前导 '/' 的值会拼出 "http://ip:8080api/..."（端口段
        # 粘连路径，httpx 直接抛非法端口 → 健康 Pod 被探死无限重部署）
        for field in ("sse_path", "health_path"):
            value = getattr(self, field) or ""
            if value and not value.startswith("/"):
                object.__setattr__(self, field, f"/{value}")
        # envFrom 空列表归一 None(指纹滤 None;[] 不入库不入快照)
        if self.agent_env_from == []:
            object.__setattr__(self, "agent_env_from", None)

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
