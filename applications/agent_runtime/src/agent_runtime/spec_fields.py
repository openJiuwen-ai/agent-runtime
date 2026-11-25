# coding: utf-8
"""template 字段分类（SM 与 RM 共享的静态定义，非跨模块数据交换）。

- DEPLOY_FIELDS   deploy 子集（A 类字段）：值被烘焙进运行中的 Pod，变更需日落。
- POLICY_FIELDS   策略字段（B 类）：控制面读时使用，变更不日落老 Pod。

2026-09 统一规范形起，容器级配置以**整体**进指纹：
``main_container`` / ``sidecars``（containers.py canonical，缺省键全填满）。
**加/改容器字段不再动本文件**——canonical 整体序列化进 deploy_ver,
只需改 containers.py（canonical 定义）+ RM 渲染分支。RM 侧对旧缓存
（缺新键的 pod_spec_json）以 containers.normalize_pod_spec 补缺省后
得到一致的新结构；跨版本保留已存 hash 还需 deploy_fingerprint 的空字段投影。

deploy_ver = DEPLOY_VER_FIELDS 的 hash 指纹（不含 kubeconfig——虽在 deploy
子集但例外：只影响新 deploy 操作，不日落）。SM（Template.deploy_ver）与 RM
（pod_spec 指纹）必须用同一字段集与算法（util.fingerprint）。
"""

from __future__ import annotations

from typing import Any

from .containers import normalize_pod_spec
from .util import fingerprint

DEPLOY_FIELDS: tuple[str, ...] = (
    # Pod 级字段（模板表模板级行列）
    "namespace",
    "node_name",
    "fs_group",            # Pod 级 securityContext.fsGroup(变更需重部署 Pod)
    "pod_name",
    "sse_path",             # gateway 直连 URL 路径段（RM 拼 pod_sse_url）
    # 容器级（canonical 整体；主容器含 nfs/sse 端口/探针等,sidecars 为
    # name 升序 canonical 列表,None = 无 sidecar）
    "main_container",
    "sidecars",
)

# deploy 指纹涵盖字段（deploy 子集 + ready 超时参数——影响 deploy 行为与版本）
DEPLOY_VER_FIELDS: tuple[str, ...] = DEPLOY_FIELDS + ("ready_timeout", "ready_poll_interval")

# 策略字段（B 类）
POLICY_FIELDS: tuple[str, ...] = (
    "scope_concurrency",
    "pod_concurrency",
    "session_ttl",
    "pod_ttl",
    "min_idle_pods",
)


def deploy_fingerprint(spec: dict[str, Any]) -> str:
    """SM/RM 共用部署指纹；无 Secret 时兼容新增该字段之前的版本。

    缺省补齐只解决旧配置重新计算的一致性。已注册 Pod 的 hash 不会重算，
    因此空 secret_mounts 只在指纹投影中省略；非空挂载完整参与计算。
    原始配置和渲染规范形保留字段，调用方的字典不会被修改。
    """
    normalized = normalize_pod_spec(spec)
    containers = [normalized.get("main_container"),
                  *(normalized.get("sidecars") or [])]
    for container in containers:
        if isinstance(container, dict) and container.get("secret_mounts") == []:
            container.pop("secret_mounts")
    return fingerprint({name: normalized.get(name) for name in DEPLOY_VER_FIELDS})
