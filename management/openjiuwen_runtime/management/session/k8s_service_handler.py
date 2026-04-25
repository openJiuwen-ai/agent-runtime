# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""K8s Pod 部署（kubernetes_asyncio），与业务层 ServiceHandler 解耦。生产环境用于 deploy/delete，测试可 Mock。"""

from __future__ import annotations

import asyncio
import re
import secrets
import string
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException

from openjiuwen_runtime.foundation.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PodDeployInfo:
    """Pod 部署成功后的信息，字段对应 `kubectl get pods -o wide` 的各列。"""

    pod_name: str
    namespace: str
    port: int
    pod_ip: str
    host_ip: Optional[str] = None
    node_name: Optional[str] = None


class K8sServiceHandler:
    """仅负责 Pod 创建/删除。业务侧可组合到 ServiceHandler 的 deploy/delete。"""

    _MAX_PREFIX_LEN = 47
    _NAME_INVALID_CHARS = re.compile(r"[^a-z0-9-]+")

    def __init__(
        self,
        image: str,
        *,
        name_prefix: str = "jiuwenclaw",
        namespace: str = "default",
        container_name: str = "jiuwenclaw-agentserver",
        container_port: int = 18092,
        port_name: str = "http1",
        image_pull_policy: str = "IfNotPresent",
        env_vars: Optional[Dict[str, str]] = None,
        extra_labels: Optional[Dict[str, str]] = None,
        restart_policy: str = "Always",
        readiness_initial_delay: int = 5,
        readiness_period: int = 10,
        kubeconfig: Optional[str] = None,
        ready_timeout: float = 300.0,
        ready_poll_interval: float = 2.0,
        delete_grace_period: int = 30,
        delete_timeout: float = 120.0,
        delete_poll_interval: float = 1.0,
    ):
        if not image:
            raise ValueError("image is required")

        self._image = image
        self._name_prefix = self._sanitize_prefix(name_prefix)
        self._namespace = namespace
        self._container_name = container_name
        self._container_port = int(container_port)
        self._port_name = port_name
        self._image_pull_policy = image_pull_policy
        self._env_vars: Dict[str, str] = dict(env_vars or {})
        self._extra_labels: Dict[str, str] = dict(extra_labels or {})
        self._restart_policy = restart_policy
        self._readiness_initial_delay = int(readiness_initial_delay)
        self._readiness_period = int(readiness_period)
        self._kubeconfig = kubeconfig
        self._ready_timeout = float(ready_timeout)
        self._ready_poll_interval = float(ready_poll_interval)
        self._delete_grace_period = int(delete_grace_period)
        self._delete_timeout = float(delete_timeout)
        self._delete_poll_interval = float(delete_poll_interval)

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

    def _generate_pod_name(self) -> str:
        return f"{self._name_prefix}-{self._random_suffix(10)}-{self._random_suffix(5)}"

    async def _ensure_config(self) -> None:
        if self._config_loaded:
            return
        try:
            config.load_incluster_config()
        except config.ConfigException:
            if self._kubeconfig:
                await config.load_kube_config(config_file=self._kubeconfig)
            else:
                await config.load_kube_config()
        self._config_loaded = True

    def _build_pod_body(self, pod_name: str) -> client.V1Pod:
        env_list = [client.V1EnvVar(name=k, value=str(v)) for k, v in self._env_vars.items()]
        labels: Dict[str, str] = {"app": pod_name}
        if self._extra_labels:
            labels.update(self._extra_labels)

        container = client.V1Container(
            name=self._container_name,
            image=self._image,
            image_pull_policy=self._image_pull_policy,
            ports=[client.V1ContainerPort(name=self._port_name, container_port=self._container_port)],
            env=env_list or None,
            readiness_probe=client.V1Probe(
                tcp_socket=client.V1TCPSocketAction(port=self._container_port),
                initial_delay_seconds=self._readiness_initial_delay,
                period_seconds=self._readiness_period,
            ),
        )

        return client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name, namespace=self._namespace, labels=labels
            ),
            spec=client.V1PodSpec(containers=[container], restart_policy=self._restart_policy),
        )

    async def deploy(self) -> PodDeployInfo:
        await self._ensure_config()

        pod_name = self._generate_pod_name()
        body = self._build_pod_body(pod_name)
        logger.info(
            "Creating pod: name=%s, namespace=%s, image=%s", pod_name, self._namespace, self._image
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
            return PodDeployInfo(
                pod_name=pod_name,
                namespace=self._namespace,
                port=self._container_port,
                pod_ip=pod_ip,
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

            if (
                phase == "Running"
                and all_containers_ready
                and ready_cond_true
                and pod_ip
            ):
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
                raise TimeoutError(
                    f"Pod {pod_name} not Running 1/1 within {self._ready_timeout}s "
                    f"(phase={phase!r}, last_reason={last_reason!r})"
                )
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
                raise TimeoutError(
                    f"Pod {pod_name} not deleted within {self._delete_timeout}s"
                )
            await asyncio.sleep(self._delete_poll_interval)


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
