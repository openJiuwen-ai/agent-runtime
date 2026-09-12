# coding: utf-8
"""Session Manager 数据模型（template 业务视图 / pod_spec 派生）。

template 字段定义见 HLD §3.1「数据结构定义」。DB 列名与 wire 术语同名
(2026-09 起统一;曾用 EE 兼容名 session_concurrency/service_concurrency/
service_ttl/min_idle_services,存量库须 RENAME COLUMN)。

2026-09 统一规范形:容器级配置(主/sidecar)以 containers.py canonical
(dict,13 键全填满)携带——``main_container`` 单容器 + ``sidecars`` 列表,
不再平铺 ~23 个 agent_* 字段(历史包袱,加字段要改六处的根因)。

scope 定义(scope_id/index/引用模板/路由规则集)由 config_sync 全量下发,
见 ``routing.py``(RoutingScopeDef)与 ``routing_scope`` 表——不再由
(group_id, bot_id) 二元组派生。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..containers import (
    MAIN_ROLE,
    default_main_container,
    main_health_path,
    main_sse_port,
    normalize_container,
    normalize_containers,
)
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
    # deploy 子集（A 类）——Pod 级字段
    namespace: str = "default"
    node_name: str | None = None
    # Pod 级 securityContext.fsGroup(wire 键 fsGroup,与 nodeName 同款模板级;
    # kubelet 卷属主修正——NFS 卷属主问题的官方修法;None = 不设)
    fs_group: int | None = None
    pod_name: str = "agentserver"          # Pod 名前缀（pod_id = 前缀-随机后缀）
    sse_path: str = "/sse"
    ready_timeout: int = 300               # deploy 等 Ready 的超时（秒）
    ready_poll_interval: int = 2
    # 主容器（canonical,见 containers.py;default = 空镜像哨兵,与旧
    # agent_image="" 同语义——未配置模板渲染空镜像,由上层拒绝/覆盖)
    main_container: dict[str, Any] = field(default_factory=default_main_container)
    # 同 Pod sidecar 容器列表(canonical;None 与 [] 统一归一为 None——
    # fingerprint 只滤 None,[] 会扰动指纹)
    sidecars: list[dict[str, Any]] | None = None
    # deploy 凭证（B 类例外：只影响新 deploy，不日落）
    kubeconfig: str | None = None
    # 元信息
    template_name: str = ""
    description: str = ""
    enabled: bool = True
    message_timeout: int = 600
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 规范形收敛:payload/DB 行/快照 JSON/测试手搓全部构造路径统一于此
        # (指纹/DB/快照三处形态唯一;canonical 幂等 → 显式默认 == 省略)。
        object.__setattr__(self, "main_container",
                           normalize_container(self.main_container,
                                               role=MAIN_ROLE))
        object.__setattr__(self, "sidecars", normalize_containers(self.sidecars))
        # 路径字段归一：缺前导 '/' 的值会拼出 "http://ip:8080api/..."（端口段
        # 粘连路径，httpx 直接抛非法端口 → 健康 Pod 被探死无限重部署）
        value = self.sse_path or ""
        if value and not value.startswith("/"):
            object.__setattr__(self, "sse_path", f"/{value}")

    # -------------------------------------------------------------- 兼容只读派生

    @property
    def agent_image(self) -> str:
        """主容器镜像(UI/诊断摘要兼容键;不参与指纹与序列化)。"""
        return str(self.main_container.get("image") or "")

    @property
    def sse_port(self) -> int:
        """gateway 直连 Pod 的 SSE 端口(canonical main ports 的 name=sse 项)。"""
        return main_sse_port(self.main_container)

    @property
    def health_path(self) -> str:
        """readiness/健康探测路径(与主容器探针同源)。"""
        return main_health_path(self.main_container)

    @property
    def container_name(self) -> str:
        return str(self.main_container.get("name") or "agent")

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
