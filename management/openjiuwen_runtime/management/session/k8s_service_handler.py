# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""K8s Pod 部署（kubernetes_asyncio），与业务层 ServiceHandler 解耦。生产环境用于 deploy/delete，测试可 Mock。"""

from __future__ import annotations

import asyncio
import re
import secrets
import string
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException

from openjiuwen_runtime.foundation.log import get_logger

logger = get_logger(__name__)

# Pod 标签选择器常量（用于监控）
POD_LABEL_KEY = "jiuwenclaw-component"
POD_LABEL_VALUE = "agentserver"
POD_LABEL_SELECTOR = f"{POD_LABEL_KEY}={POD_LABEL_VALUE}"
GATEWAY_ID_LABEL_KEY = "jiuwenclaw-gateway-id"


@dataclass(frozen=True)
class PodDeployInfo:
    """Pod 部署成功后的信息，字段对应 `kubectl get pods -o wide` 的各列。"""

    pod_name: str
    namespace: str
    pod_ip: str
    containers: Dict[str, "ContainerEndpoint"]
    port: Optional[int] = None
    host_ip: Optional[str] = None
    node_name: Optional[str] = None


@dataclass(frozen=True)
class ContainerEndpoint:
    container_name: str
    port: int
    port_name: str


@dataclass(frozen=True)
class PodStatusInfo:
    """Pod 状态信息（用于监控接口）。"""

    pod_name: str  # Pod 名称
    namespace: str  # 命名空间
    status: str  # 归一化状态（Running/Pending/Failed/Terminated/CrashLoopBackOff 等）
    phase: str  # K8s 原生 phase（Running/Pending/Failed/Succeeded/Unknown）
    pod_ip: Optional[str]  # Pod IP
    host_ip: Optional[str]  # 宿主机 IP
    node_name: Optional[str]  # 节点名
    reason: Optional[str]  # 异常原因（Waiting/Terminated 等状态的 reason）
    message: Optional[str]  # 详细信息（Waiting/Terminated 等状态的 message）
    restart_count: int  # 容器重启次数
    deletion_timestamp: Optional[str]  # 删除时间戳（用于检测 Terminating 状态）

    # 可选：metrics-server 数据
    cpu_usage_cores: Optional[float] = None  # CPU 使用量（核数）
    memory_usage_bytes: Optional[int] = None  # 内存使用量（字节）


@dataclass(frozen=True)
class HostPathMount:
    """容器内的 hostPath 挂载声明（对应 ``docker run -v HOST:CONTAINER[:ro]``）。

    - ``host_path``: 宿主机绝对路径，写入 ``V1HostPathVolumeSource.path``
    - ``mount_path``: 容器内目标路径
    - ``read_only``: True 时挂载为只读
    - ``host_path_type``: 对应 V1HostPathVolumeSource.type，缺省不校验路径类型
    """

    host_path: str
    mount_path: str
    read_only: bool = False
    host_path_type: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.host_path:
            raise ValueError("HostPathMount.host_path is required")
        if not self.mount_path:
            raise ValueError("HostPathMount.mount_path is required")


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    port: int = 18092
    port_name: str = "http1"
    image_pull_policy: str = "IfNotPresent"
    env_vars: Optional[Dict[str, str]] = None
    # ---- 特权 / 安全相关（对应 docker run --cap-add / --security-opt / --privileged） ----
    privileged: bool = False
    """开启等价于 ``docker run --privileged``，会覆盖 capabilities/seccomp/apparmor 等更细字段。"""
    capabilities_add: List[str] = field(default_factory=list)
    """例如 ``["SYS_ADMIN", "NET_ADMIN"]``，对应 ``--cap-add``。"""
    capabilities_drop: List[str] = field(default_factory=list)
    """对应 ``--cap-drop``。"""
    seccomp_unconfined: bool = False
    """对应 ``--security-opt seccomp=unconfined``。"""
    apparmor_unconfined: bool = False
    """对应 ``--security-opt apparmor=unconfined``，会被翻译为 Pod 上的 annotation。"""
    proc_mount_unmasked: bool = False
    """对应 ``--security-opt systempaths=unconfined``，需 K8s 启用 ProcMountType feature gate；
    若集群不支持，建议改用 ``privileged=True``。"""
    run_as_user: Optional[int] = None
    run_as_group: Optional[int] = None
    allow_privilege_escalation: Optional[bool] = None
    run_as_non_root: Optional[bool] = None

    # ---- 端口暴露 ----
    host_port: Optional[int] = None
    """对应 ``docker run -p HOST_PORT:CONTAINER_PORT``，在 K8s 中即 ``containerPort.hostPort``。"""
    # ---- hostPath 挂载（对应 ``docker run -v HOST:CTR``） ----
    host_path_mounts: List[HostPathMount] = field(default_factory=list)

    cpu_request: Optional[str] = None
    memory_request: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory_limit: Optional[str] = None

    readiness_probe_type: Optional[str] = None
    readiness_initial_delay: int = 5
    readiness_period: int = 10
    readiness_timeout_seconds: int = 3

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("container name is required")
        if not self.image:
            raise ValueError("container image is required")
        port = int(self.port)
        if port <= 0:
            raise ValueError("container port must be positive")
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "env_vars", dict(self.env_vars or {}))
        object.__setattr__(self, "capabilities_add", list(self.capabilities_add or []))
        object.__setattr__(self, "capabilities_drop", list(self.capabilities_drop or []))
        object.__setattr__(self, "host_path_mounts", list(self.host_path_mounts or []))
        if self.host_port is not None:
            hp = int(self.host_port)
            if hp <= 0 or hp > 65535:
                raise ValueError("host_port must be in (0, 65535]")
            object.__setattr__(self, "host_port", hp)


class K8sServiceHandler:
    """仅负责 Pod 创建/删除。业务侧可组合到 ServiceHandler 的 deploy/delete。"""

    _MAX_PREFIX_LEN = 47
    _NAME_INVALID_CHARS = re.compile(r"[^a-z0-9-]+")

    def __init__(
            self,
            *,
            containers: List[ContainerSpec],
            name_prefix: str = "jiuwenclaw",
            namespace: str = "default",
            pod_name: str = None,
            extra_labels: Optional[Dict[str, str]] = None,
            owner_reference: Optional[client.V1OwnerReference] = None,  # Pod ownerReference，用于 owner 删除时级联清理
            restart_policy: str = "Always",
            kubeconfig: Optional[str] = None,
            ready_timeout: float = 300.0,
            ready_poll_interval: float = 2.0,
            delete_grace_period: int = 30,
            delete_timeout: float = 120.0,
            delete_poll_interval: float = 1.0,
            mount_type: Optional[str] = None,
            pvc: Optional[str] = None,
            nfs_server: Optional[str] = None,  # NFS 服务器地址
            nfs_path: Optional[str] = None,  # NFS 共享路径
            mount_path: Optional[str] = None,  # 容器内挂载路径
            mode: str = "product",  # 运行环境模式：支持 dev / product 两种值
            node_name: Optional[str] = None,  # 强制调度到指定节点
    ):
        if not containers:
            raise ValueError("containers must not be empty")

        ports = [int(c.port) for c in containers]
        if len(set(ports)) != len(ports):
            raise ValueError("container ports must be unique in one pod")

        if mount_type == "nfs":
            if not nfs_server or not nfs_path or not mount_path:
                raise ValueError("nfs_server, nfs_path and mount_path are required when mount_type is 'nfs'")
        elif mount_type == "pvc":
            if not pvc:
                raise ValueError("pvc is required when mount_type is 'pvc'")

        self._containers = list(containers)
        self._name_prefix = pod_name if pod_name else self._sanitize_prefix(name_prefix)
        self._namespace = namespace
        self._extra_labels: Dict[str, str] = dict(extra_labels or {})
        self._owner_reference = owner_reference
        self._restart_policy = restart_policy
        self._kubeconfig = kubeconfig
        self._ready_timeout = float(ready_timeout)
        self._ready_poll_interval = float(ready_poll_interval)
        self._delete_grace_period = int(delete_grace_period)
        self._delete_timeout = float(delete_timeout)
        self._delete_poll_interval = float(delete_poll_interval)
        self._mount_type = mount_type
        self._pvc = pvc
        self._nfs_server = nfs_server
        self._nfs_path = nfs_path
        self._mount_path = mount_path
        self._mode = mode
        self._node_name = node_name
        self._pod_name: Optional[str] = None
        self._config_loaded = False

    @property
    def pod_name(self) -> Optional[str]:
        return self._pod_name

    @classmethod
    def _sanitize_prefix(cls, prefix: str) -> str:
        if not prefix:
            raise ValueError("name_prefix must not be empty")
        cleaned = cls._NAME_INVALID_CHARS.sub("-", prefix.lower()).strip("-")
        if not cleaned:
            raise ValueError(f"name_prefix {prefix!r} contains no valid DNS-1123 chars")
        return cleaned[: cls._MAX_PREFIX_LEN]

    @staticmethod
    def _random_suffix(length: int) -> str:
        alphabet = string.ascii_lowercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @classmethod
    def _build_nfs_volume_name(cls, name: str) -> str:
        # K8s volume 名需符合 DNS-1123 label：小写字母数字或 '-'，长度 <= 63
        sanitized = cls._NAME_INVALID_CHARS.sub("-", (name or "").lower()).strip("-")
        # 预留 "nfs-" 前缀 4 字符，整体限制 63 字符
        return f"nfs-{sanitized[:59]}"

    @classmethod
    def _build_pvc_volume_name(cls, name: str) -> str:
        # K8s volume 名需符合 DNS-1123 label：小写字母数字或 '-'，长度 <= 63
        sanitized = cls._NAME_INVALID_CHARS.sub("-", (name or "").lower()).strip("-")
        # 预留 "nfs-" 前缀 4 字符，整体限制 63 字符
        return f"pvc-{sanitized[:59]}"

    @classmethod
    def _build_host_path_volume_name(cls, name: str, idx: int, mount_idx: int) -> str:
        # 预留 "hp-" 前缀和索引后缀，避免同一 Pod 内多容器、多挂载重名。
        sanitized = cls._NAME_INVALID_CHARS.sub("-", (name or "").lower()).strip("-")
        base = sanitized or f"c{idx}"
        suffix = f"-{idx}-{mount_idx}"
        return f"hp-{base[: 63 - len('hp-') - len(suffix)]}{suffix}"

    @classmethod
    def _build_security_context(cls, spec: ContainerSpec) -> Optional[client.V1SecurityContext]:
        capabilities = None
        if spec.capabilities_add or spec.capabilities_drop:
            capabilities = client.V1Capabilities(
                add=spec.capabilities_add or None,
                drop=spec.capabilities_drop or None,
            )

        seccomp_profile = None
        if spec.seccomp_unconfined:
            seccomp_profile = client.V1SeccompProfile(type="Unconfined")

        fields = {
            "privileged": spec.privileged,
            "capabilities": capabilities,
            "seccomp_profile": seccomp_profile,
            "proc_mount": "Unmasked" if spec.proc_mount_unmasked else None,
            "run_as_user": spec.run_as_user,
            "run_as_group": spec.run_as_group,
            "allow_privilege_escalation": spec.allow_privilege_escalation,
            "run_as_non_root": spec.run_as_non_root,
        }
        if all(value is None or value is False for value in fields.values()):
            return None
        return client.V1SecurityContext(**fields)

    @classmethod
    def _build_container_resources(cls, spec: ContainerSpec) -> Optional[client.V1ResourceRequirements]:
        requests = {}
        if spec.cpu_request:
            requests["cpu"] = spec.cpu_request
        if spec.memory_request:
            requests["memory"] = spec.memory_request

        limits = {}
        if spec.cpu_limit:
            limits["cpu"] = spec.cpu_limit
        if spec.memory_limit:
            limits["memory"] = spec.memory_limit

        if not requests and not limits:
            return None

        return client.V1ResourceRequirements(
            requests=requests or None,
            limits=limits or None
        )

    @classmethod
    def _build_readiness_probe(cls, spec: ContainerSpec) -> Optional[client.V1Probe]:
        if spec.readiness_probe_type == "http":
            return client.V1Probe(
                http_get=client.V1HTTPGetAction(
                    port=spec.port,
                    path="/healthz",
                ),
                initial_delay_seconds=spec.readiness_initial_delay,
                period_seconds=spec.readiness_period,
                timeout_seconds=spec.readiness_timeout_seconds,
            )
        elif spec.readiness_probe_type == "websockets":
            return client.V1Probe(
                _exec=client.V1ExecAction(
                    command=[
                        "python",
                        "/app/jiuwenclaw/tools/ws_probe.py"
                    ]
                ),
                initial_delay_seconds=spec.readiness_initial_delay,
                period_seconds=spec.readiness_period,
                timeout_seconds=spec.readiness_timeout_seconds,
            )
        elif spec.readiness_probe_type == "tcp":
            return client.V1Probe(
                tcp_socket=client.V1TCPSocketAction(port=spec.port),
                    initial_delay_seconds=spec.readiness_initial_delay,
                    period_seconds=spec.readiness_period,
                    timeout_seconds=spec.readiness_timeout_seconds,
            )
        else:
            return None

    def _generate_pod_name(self) -> str:
        return f"{self._name_prefix}-{self._random_suffix(10)}-{self._random_suffix(5)}"

    async def _ensure_config(self) -> None:
        if self._config_loaded:
            return
        try:
            config.load_incluster_config()
            logger.debug("K8s 已加载 in-cluster 配置")
        except config.ConfigException:
            if self._kubeconfig:
                await config.load_kube_config(config_file=self._kubeconfig)
                logger.debug("K8s 已加载 kubeconfig: %s", self._kubeconfig)
            else:
                await config.load_kube_config()
                logger.debug("K8s 已加载默认 kubeconfig")
        self._config_loaded = True

    def _build_pod_body(self, pod_name: str) -> client.V1Pod:
        labels: Dict[str, str] = {"app": pod_name}
        if self._extra_labels:
            labels.update(self._extra_labels)

        annotations: Dict[str, str] = {}
        volumes: List[client.V1Volume] = []

        if self._mount_type == "nfs":
            pod_volume_name = self._build_nfs_volume_name(pod_name)
            volumes.append(
                client.V1Volume(
                    name=pod_volume_name,
                    nfs=client.V1NFSVolumeSource(
                        server=self._nfs_server,
                        path=self._nfs_path,
                    ),
                )
            )
        elif self._mount_type == "pvc":
            pod_volume_name = self._build_pvc_volume_name(pod_name)
            volumes.append(
                client.V1Volume(
                    name=pod_volume_name,
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=self._pvc,
                        read_only=False,
                    )
                )
            )

        pod_containers: List[client.V1Container] = []
        for idx, spec in enumerate(self._containers):
            env_list = [client.V1EnvVar(name=k, value=str(v)) for k, v in spec.env_vars.items()]

            container_volume_mounts: List[client.V1VolumeMount] = []
            if self._mount_type == "nfs" or self._mount_type == "pvc":
                container_volume_mounts.append(
                    client.V1VolumeMount(
                        name=pod_volume_name,
                        mount_path=self._mount_path,
                    )
                )

            for mount_idx, mount in enumerate(spec.host_path_mounts):
                volume_name = self._build_host_path_volume_name(spec.name, idx, mount_idx)
                volumes.append(
                    client.V1Volume(
                        name=volume_name,
                        host_path=client.V1HostPathVolumeSource(
                            path=mount.host_path,
                            type=mount.host_path_type,
                        ),
                    )
                )
                container_volume_mounts.append(
                    client.V1VolumeMount(
                        name=volume_name,
                        mount_path=mount.mount_path,
                        read_only=mount.read_only,
                    )
                )

            if spec.apparmor_unconfined:
                annotations[
                    f"container.apparmor.security.beta.kubernetes.io/{spec.name}"
                ] = "unconfined"

            pod_containers.append(
                client.V1Container(
                    name=spec.name,
                    image=spec.image,
                    image_pull_policy=spec.image_pull_policy,
                    ports=[
                        client.V1ContainerPort(
                            name=spec.port_name,
                            container_port=spec.port,
                            host_port=spec.host_port,
                        )
                    ],
                    env=env_list or None,
                    volume_mounts=container_volume_mounts or None,
                    resources=self._build_container_resources(spec),
                    security_context=self._build_security_context(spec),
                    readiness_probe=self._build_readiness_probe(spec),
                )
            )

        pod_security_context = None
        if self._mode == "dev":
            pod_security_context = client.V1PodSecurityContext(
                fs_group=0
            )
        return client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self._namespace,
                labels=labels,
                annotations=annotations or None,
                owner_references=[self._owner_reference] if self._owner_reference else None,
            ),
            spec=client.V1PodSpec(
                containers=pod_containers,
                restart_policy=self._restart_policy,
                volumes=volumes or None,
                node_name=self._node_name if self._mode == "dev" else None,
                security_context=pod_security_context,
            ),
        )

    async def deploy(self) -> PodDeployInfo:
        # 1) 加载集群访问配置 2) 创建 Pod 3) 轮询至 Running + Ready + 有 podIP
        await self._ensure_config()

        pod_name = self._generate_pod_name()
        body = self._build_pod_body(pod_name)
        image_list = ",".join(c.image for c in self._containers)
        container_meta = ",".join(f"{c.name}:{c.port}" for c in self._containers)
        logger.info(
            "K8s 创建 Pod: name=%s namespace=%s containers=%s images=%s",
            pod_name,
            self._namespace,
            container_meta,
            image_list,
        )

        api_client = client.ApiClient()
        try:
            core = client.CoreV1Api(api_client)

            try:
                await core.create_namespaced_pod(namespace=self._namespace, body=body)
            except ApiException as exc:
                if exc.status == 409:
                    retry_name = self._generate_pod_name()
                    logger.warning("Pod name conflict %s, retrying with %s", pod_name, retry_name)
                    body.metadata.name = retry_name
                    if body.metadata.labels is None:
                        body.metadata.labels = {}
                    body.metadata.labels["app"] = retry_name
                    await core.create_namespaced_pod(namespace=self._namespace, body=body)
                    pod_name = retry_name
                else:
                    raise

            self._pod_name = pod_name
            pod_ip, host_ip, node_name = await self._wait_running_ready(core, pod_name)
            logger.info(
                "Pod ready: name=%s, pod_ip=%s, node=%s",
                pod_name,
                pod_ip,
                node_name,
            )
            endpoints = {
                c.name: ContainerEndpoint(
                    container_name=c.name,
                    port=c.port,
                    port_name=c.port_name,
                )
                for c in self._containers
            }
            return PodDeployInfo(
                pod_name=pod_name,
                namespace=self._namespace,
                pod_ip=pod_ip,
                containers=endpoints,
                port=self._containers[0].port,
                host_ip=host_ip,
                node_name=node_name,
            )
        finally:
            await api_client.close()

    async def _wait_running_ready(
            self, core: client.CoreV1Api, pod_name: str
    ) -> Tuple[str, Optional[str], Optional[str]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._ready_timeout
        last_reason = ""

        while True:
            pod = await core.read_namespaced_pod(name=pod_name, namespace=self._namespace)
            status = pod.status
            phase = (status.phase or "") if status else ""

            if phase in ("Failed", "Succeeded"):
                raise RuntimeError(
                    f"Pod {pod_name} entered terminal phase {phase}: {last_reason}"
                )

            container_statuses = (status.container_statuses or []) if status else []
            all_containers_ready = bool(container_statuses) and all(
                bool(cs.ready) for cs in container_statuses
            )
            ready_cond_true = bool(status) and any(
                c.type == "Ready" and c.status == "True" for c in (status.conditions or [])
            )
            pod_ip = (status.pod_ip if status else None) or ""

            is_running_and_containers_ready = phase == "Running" and all_containers_ready
            is_ready_with_ip = ready_cond_true and pod_ip
            if is_running_and_containers_ready and is_ready_with_ip:
                host_ip = status.host_ip if status else None
                node_name = getattr(pod.spec, "node_name", None) if pod.spec else None
                return pod_ip, host_ip, node_name

            for cs in container_statuses:
                waiting = getattr(cs.state, "waiting", None) if cs.state else None
                reason = getattr(waiting, "reason", None) if waiting else None
                if reason:
                    last_reason = (
                        f"container={cs.name} waiting={reason} "
                        f"msg={getattr(waiting, 'message', '') or ''}"
                    )

            if loop.time() >= deadline:
                logger.error(
                    "K8s Pod 就绪超时: name=%s phase=%s last_reason=%s", pod_name, phase, last_reason
                )
                raise TimeoutError(
                    f"Pod {pod_name} not Running/Ready within {self._ready_timeout}s "
                    f"(phase={phase!r}, last_reason={last_reason!r})"
                )
            logger.debug("K8s 等待 Pod 就绪: name=%s phase=%s", pod_name, phase)
            await asyncio.sleep(self._ready_poll_interval)

    async def delete(self) -> str:
        if not self._pod_name:
            raise RuntimeError("delete() called before deploy() or pod already deleted")

        pod_name = self._pod_name
        await self._ensure_config()
        logger.info("Deleting pod: name=%s, namespace=%s", pod_name, self._namespace)

        api_client = client.ApiClient()
        try:
            core = client.CoreV1Api(api_client)
            try:
                await core.delete_namespaced_pod(
                    name=pod_name,
                    namespace=self._namespace,
                    body=client.V1DeleteOptions(
                        grace_period_seconds=self._delete_grace_period,
                        propagation_policy="Foreground",
                    ),
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise
                logger.info("Pod %s already absent; treating delete as idempotent", pod_name)
            await self._wait_pod_deleted(core, pod_name)
        finally:
            await api_client.close()

        logger.info("Pod deleted: name=%s", pod_name)
        self._pod_name = None
        return pod_name

    async def _wait_pod_deleted(self, core: client.CoreV1Api, pod_name: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._delete_timeout

        while True:
            try:
                await core.read_namespaced_pod(name=pod_name, namespace=self._namespace)
            except ApiException as exc:
                if exc.status == 404:
                    return
                raise

            if loop.time() >= deadline:
                logger.error("K8s Pod 删除超时: name=%s", pod_name)
                raise TimeoutError(
                    f"Pod {pod_name} not deleted within {self._delete_timeout}s"
                )
            await asyncio.sleep(self._delete_poll_interval)

    @staticmethod
    async def cleanup_all_agentserver_pods(
            namespace: str,
            kubeconfig: Optional[str] = None,
            label_selector: str = "",
            grace_period: int = 30,
    ) -> int:
        """按 label selector 批量删除所有匹配的 Pod（主备切换时清理旧 PRIMARY 遗留的 Pod）。

        返回已提交删除的 Pod 数量。
        """
        try:
            config.load_incluster_config()
        except config.ConfigException:
            if kubeconfig:
                await config.load_kube_config(config_file=kubeconfig)
            else:
                await config.load_kube_config()
        api_client = client.ApiClient()
        deleted = 0
        try:
            core = client.CoreV1Api(api_client)
            pods = await core.list_namespaced_pod(
                namespace=namespace, label_selector=label_selector,
            )
            pod_names = [
                p.metadata.name for p in (pods.items or [])
                if p.metadata and p.metadata.name
            ]
            if not pod_names:
                logger.info("无匹配 Pod 需清理: namespace=%s selector=%s", namespace, label_selector)
                return 0
            logger.info("开始批量清理 Pod: namespace=%s count=%s", namespace, len(pod_names))
            for name in pod_names:
                try:
                    await core.delete_namespaced_pod(
                        name=name,
                        namespace=namespace,
                        body=client.V1DeleteOptions(
                            grace_period_seconds=grace_period,
                            propagation_policy="Foreground",
                        ),
                    )
                    deleted += 1
                except ApiException as exc:
                    if exc.status != 404:
                        logger.error("删除 Pod 失败: name=%s err=%s", name, exc)
            logger.info("批量清理完成: deleted=%s", deleted)
        finally:
            await api_client.close()
        return deleted

    @staticmethod
    async def monitor_pods_status(
        namespace: str,
        label_selector: str = "",
        kubeconfig: Optional[str] = None,
        include_metrics: bool = False,
    ) -> List[PodStatusInfo]:
        """监测所有匹配的 Pod 状态。

        Args:
            namespace: K8s 命名空间
            label_selector: Pod 标签选择器（如 "app=jiuwenclaw-agentserver"）
            kubeconfig: 可选的 kubeconfig 路径
            include_metrics: 是否包含 metrics-server 数据（CPU/Memory 使用率）

        Returns:
            Pod 状态信息列表，每个元素包含 pod_name、status、phase、reason、pod_ip 等
        """
        # 1. 加载 K8s 配置
        try:
            config.load_incluster_config()
            logger.debug("K8s 已加载 in-cluster 配置（监控接口）")
        except config.ConfigException:
            if kubeconfig:
                await config.load_kube_config(config_file=kubeconfig)
                logger.debug("K8s 已加载 kubeconfig: %s（监控接口）", kubeconfig)
            else:
                await config.load_kube_config()
                logger.debug("K8s 已加载默认 kubeconfig（监控接口）")

        api_client = client.ApiClient()
        try:
            core = client.CoreV1Api(api_client)

            # 2. 获取所有匹配的 Pod
            pods = await core.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            )

            if not pods.items:
                logger.debug("监控接口未找到匹配的 Pod: namespace=%s selector=%s", namespace, label_selector)
                return []

            logger.debug(
                "监控接口查询到 Pod: namespace=%s selector=%s count=%s",
                namespace, label_selector, len(pods.items)
            )

            # 3. 遍历 Pod，提取状态信息并归一化
            pod_statuses: List[PodStatusInfo] = []
            for pod in pods.items:
                if not pod.metadata or not pod.metadata.name:
                    continue

                pod_name = pod.metadata.name
                pod_namespace = pod.metadata.namespace or namespace
                deletion_timestamp = None
                if pod.metadata.deletion_timestamp:
                    deletion_timestamp = pod.metadata.deletion_timestamp.isoformat()

                # 提取基本信息
                phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
                pod_ip = (pod.status.pod_ip if pod.status else None) or None
                host_ip = (pod.status.host_ip if pod.status else None) or None
                node_name = (pod.spec.node_name if pod.spec else None) or None

                # 计算归一化状态
                status, reason, message, restart_count = K8sServiceHandler.compute_pod_status(
                    pod, phase, deletion_timestamp
                )

                # 构建 PodStatusInfo
                pod_status_info = PodStatusInfo(
                    pod_name=pod_name,
                    namespace=pod_namespace,
                    status=status,
                    phase=phase,
                    pod_ip=pod_ip,
                    host_ip=host_ip,
                    node_name=node_name,
                    reason=reason,
                    message=message,
                    restart_count=restart_count,
                    deletion_timestamp=deletion_timestamp,
                )
                pod_statuses.append(pod_status_info)
            logger.info(
                "监控接口完成 Pod 状态查询: namespace=%s total=%s running=%s",
                namespace,
                len(pod_statuses),
                sum(1 for p in pod_statuses if p.status == "Running"),
            )

            return pod_statuses

        finally:
            await api_client.close()

    @staticmethod
    def compute_pod_status(
        pod: client.V1Pod,
        phase: str,
        deletion_timestamp: Optional[str],
    ) -> tuple[str, Optional[str], Optional[str], int]:
        """计算 Pod 的归一化状态。

        Args:
            pod: K8s Pod 对象
            phase: Pod 原生 phase
            deletion_timestamp: Pod 删除时间戳

        Returns:
            (status, reason, message, restart_count) 元组
            - status: 归一化状态（Running/Pending/Failed/Terminated/CrashLoopBackOff/Terminating 等）
            - reason: 异常原因（如果有）
            - message: 详细信息（如果有）
            - restart_count: 容器重启次数
        """
        # 优先级 1: 检测 Terminating 状态
        if deletion_timestamp:
            return "Terminating", "DeletionTimestamp", "Pod is being deleted", 0

        # 获取容器状态
        container_statuses = (pod.status.container_statuses or []) if pod.status else []
        if not container_statuses:
            # 没有容器状态，直接返回 phase
            return phase, None, None, 0

        # 计算总重启次数
        total_restart_count = sum(cs.restart_count or 0 for cs in container_statuses)

        # 优先级 2: 检查容器是否处于 Waiting 状态
        for cs in container_statuses:
            if not cs.state:
                continue

            waiting = getattr(cs.state, "waiting", None)
            if waiting:
                reason = getattr(waiting, "reason", None) or "Waiting"
                message = getattr(waiting, "message", None) or ""
                # 常见的失败状态
                if reason in (
                    "ImagePullBackOff",
                    "ErrImagePull",
                    "CrashLoopBackOff",
                    "CreateContainerConfigError",
                    "InvalidImageName",
                ):
                    return reason, reason, message, total_restart_count
                # 其他 Waiting 状态
                return "Waiting", reason, message, total_restart_count

            # 优先级 3: 检查容器是否已 Terminated
            terminated = getattr(cs.state, "terminated", None)
            if terminated:
                exit_code = getattr(terminated, "exit_code", None)
                reason = getattr(terminated, "reason", None) or "Terminated"
                message = getattr(terminated, "message", None) or ""
                # 非零退出码表示错误
                if exit_code and exit_code != 0:
                    return "Error", reason, f"Exit code: {exit_code}. {message}", total_restart_count
                return "Terminated", reason, message, total_restart_count

        # 优先级 4: 返回 K8s 原生 phase
        return phase, None, None, total_restart_count


class K8sDeployController:
    """将 K8sServiceHandler 适配为 session.runtime.IDeployController。"""

    def __init__(self, k8s: K8sServiceHandler) -> None:
        self._k8s = k8s

    @property
    def resource_id(self) -> Optional[str]:
        return self._k8s.pod_name

    async def deploy(self) -> PodDeployInfo:
        return await self._k8s.deploy()

    async def delete(self) -> str:
        return await self._k8s.delete()
