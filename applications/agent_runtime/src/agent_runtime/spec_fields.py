# coding: utf-8
"""template 字段分类（SM 与 RM 共享的静态定义，非跨模块数据交换）。

- DEPLOY_FIELDS   deploy 子集（A 类字段）：值被烘焙进运行中的 Pod，变更需日落。
- POLICY_FIELDS   策略字段（B 类）：控制面读时使用，变更不日落老 Pod。

deploy_ver = DEPLOY_VER_FIELDS 的 hash 指纹（不含 kubeconfig——虽在 deploy
子集但例外：只影响新 deploy 操作，不日落）。SM（Template.deploy_ver）与 RM
（pod_spec 指纹）必须用同一字段集与算法（util.fingerprint）。
"""

from __future__ import annotations

DEPLOY_FIELDS: tuple[str, ...] = (
    "agent_image",
    "namespace",
    "node_name",
    "run_as_user",
    "run_as_group",
    "pod_name",
    "container_name",
    "container_port",
    "sse_port",
    "sse_path",
    "health_path",          # readiness 探针路径(默认 /health;真 AgentServer 为 /api/v1/health)
    "agent_env",            # Agent 容器注入的 env(如 AGENT_HTTP_ENABLED/HOST/PORT)
    "agent_env_from",       # envFrom 引用(secretRef/configMapRef;None 不进指纹——存量零扰动)
    "image_pull_policy",
    "readiness_initial_delay",
    "readiness_period",
    "nfs_server",
    "nfs_path",
    "nfs_mount_path",
    "agent_cpu_request",
    "agent_memory_request",
    "agent_cpu_limit",
    "agent_memory_limit",
    "sidecars",             # 同 Pod sidecar 容器列表(通用;首个用户 jiuwenbox)
    "agent_host_path_mounts",   # 主容器 hostPath 挂载(规范形见 mounts.py)
    "agent_configmap_mounts",   # 主容器 ConfigMap 挂载
    "agent_pvc_mounts",         # 主容器 PVC 挂载
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
