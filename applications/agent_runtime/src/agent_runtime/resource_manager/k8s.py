# coding: utf-8
"""RM 的 K8s 交互层（移植老 SDK K8sServiceHandler 的 deploy/delete/判死，asyncio 化）。

- ``RealK8sPodClient``：kubernetes_asyncio 实现（server 模式）。deploy = create +
  wait Running/Ready + 有 podIP；409 名字冲突自动重命名重试；镜像拉取失败/NotReady
  超时 → DeployFailed。
- ``FakeK8sPodClient``：进程内实现（local 模式 / 单测），可编程 Pod 状态与健康。

pod_id = K8s 随机 Pod 名（P1 撞号教训：严禁用业务 id 当实例 id）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import string
import time
from typing import Any

import httpx

from ..errors import DeployFailed
from .models import POD_LABEL_KEY, POD_LABEL_VALUE, PodDeployInfo, PodInfo

logger = logging.getLogger("agent_runtime.resource_manager")

DEFAULT_READY_TIMEOUT = 300      # deploy 等 Ready 超时（秒）
DEFAULT_READY_POLL_INTERVAL = 2  # 就绪轮询间隔（秒）
DELETE_TIMEOUT = 60
WAIT_READY_PROGRESS_SEC = 30    # _wait_ready 进度行间隔（最长 300s 不留空白）


def _random_suffix(length: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def normalize_phase(phase: str, deletion: bool, container_waiting_reasons: list[str]) -> str:
    """归一化 Pod 状态（移植 compute_pod_status 的判定优先级，精简版）。"""
    if deletion:
        return "Terminating"
    for reason in container_waiting_reasons:
        if reason in ("ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff",
                      "InvalidImageName", "CreateContainerConfigError"):
            return reason
    return phase or "Unknown"


class K8sPodClient:
    """K8s Pod 操作接口（Real/Fake 共同签名）。"""

    default_namespace: str = "default"

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def deploy(self, pod_spec: dict[str, Any]) -> PodDeployInfo:
        """create Pod + wait Running/Ready；返回物理信息。失败抛 DeployFailed。"""
        raise NotImplementedError

    async def delete(self, pod_id: str, namespace: str) -> str:
        """删除 Pod（幂等，NotFound 安全）。返回 pod_id。"""
        raise NotImplementedError

    async def get_pod(self, pod_id: str, namespace: str) -> PodInfo | None:
        raise NotImplementedError

    async def list_pods(self, namespace: str, label_selector: str) -> list[PodInfo]:
        raise NotImplementedError

    async def probe_health(self, pod_ip: str, sse_port: int) -> bool:
        """场景 N：探测 AgentServer 健康端点 GET http://{pod_ip}:{sse_port}/health。"""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"http://{pod_ip}:{sse_port}/health")
                if response.status_code != 200:
                    logger.debug("health probe non-200: ip=%s port=%s status=%s",
                                 pod_ip, sse_port, response.status_code)
                return response.status_code == 200
        except Exception as exc:  # noqa: BLE001 - 探测失败即不健康
            # 失败原因留痕（调用方 sweeper 有同节奏 WARNING，这里 DEBUG 补细节）
            logger.debug("health probe error: ip=%s port=%s error=%s: %s",
                         pod_ip, sse_port, type(exc).__name__, exc)
            return False


# ---------------------------------------------------------------- Real（kubernetes_asyncio）


class RealK8sPodClient(K8sPodClient):
    """真集群实现（server 模式）。pod_spec 字段 = Template.deploy_subset()。"""

    def __init__(self, kubeconfig: str | None = None, default_namespace: str = "default"):
        self.kubeconfig = kubeconfig
        self.default_namespace = default_namespace
        self._client: Any = None       # kubernetes_asyncio.client 模块
        self._core: Any = None         # CoreV1Api
        self._api_client: Any = None
        self._loaded = False

    async def start(self) -> None:
        if self._core is not None:
            return
        try:
            from kubernetes_asyncio import client, config
            from kubernetes_asyncio.config.config_exception import ConfigException
        except Exception as exc:  # pragma: no cover - 环境缺依赖
            raise DeployFailed(f"kubernetes_asyncio unavailable: {exc}") from exc
        try:
            try:
                # kubernetes_asyncio：load_incluster_config 是**同步**函数
                # （await 它会 TypeError→DeployFailed，in-cluster 部署必挂；
                # load_kube_config 才是协程）
                config.load_incluster_config()
            except ConfigException:
                await config.load_kube_config(config_file=self.kubeconfig)
            self._api_client = client.ApiClient()
            self._client = client
            self._core = client.CoreV1Api(self._api_client)
            self._loaded = True
        except Exception as exc:
            raise DeployFailed(f"cannot init kubernetes client: {exc}") from exc

    async def close(self) -> None:
        core, api_client = self._core, self._api_client
        self._core = self._api_client = self._client = None
        if api_client is not None:
            await api_client.close()

    # -------------------------------------------------------------- deploy

    async def deploy(self, pod_spec: dict[str, Any]) -> PodDeployInfo:
        await self.start()
        namespace = pod_spec.get("namespace") or self.default_namespace
        timeout = int(pod_spec.get("ready_timeout") or DEFAULT_READY_TIMEOUT)
        poll = float(pod_spec.get("ready_poll_interval") or DEFAULT_READY_POLL_INTERVAL)

        for _ in range(3):  # 409 名字冲突 → 重命名重试（至多 3 次）
            pod_id = f"{pod_spec.get('pod_name') or 'agentserver'}-{_random_suffix(10)}-{_random_suffix(5)}"
            body = self._build_pod_body(pod_id, pod_spec)
            logger.info("k8s create pod: name=%s namespace=%s image=%s",
                        pod_id, namespace, pod_spec.get("agent_image"))
            try:
                await self._core.create_namespaced_pod(namespace=namespace, body=body)
            except Exception as exc:
                if getattr(exc, "status", None) == 409:
                    logger.warning("k8s pod name conflict, retrying: name=%s", pod_id)
                    continue
                raise DeployFailed(f"k8s create pod failed: {exc}") from exc
            return await self._wait_ready(pod_id, namespace, timeout, poll)
        raise DeployFailed("k8s create pod failed: name conflicts exhausted")

    def _build_pod_body(self, pod_id: str, spec: dict[str, Any]) -> Any:
        c = self._client
        labels = {POD_LABEL_KEY: POD_LABEL_VALUE, "app": pod_id}
        volumes, mounts = [], []
        if spec.get("nfs_server"):
            volume_name = f"{pod_id}-nfs"
            volumes.append(c.V1Volume(
                name=volume_name,
                nfs=c.V1NFSVolumeSource(
                    server=spec["nfs_server"], path=spec.get("nfs_path") or "/",
                ),
            ))
            mounts.append(c.V1VolumeMount(
                name=volume_name, mount_path=spec.get("nfs_mount_path") or "/data",
            ))

        resources = None
        if any(spec.get(f) for f in ("agent_cpu_request", "agent_memory_request",
                                     "agent_cpu_limit", "agent_memory_limit")):
            resources = c.V1ResourceRequirements(
                requests={k: v for k, v in (
                    ("cpu", spec.get("agent_cpu_request")),
                    ("memory", spec.get("agent_memory_request")),
                ) if v} or None,
                limits={k: v for k, v in (
                    ("cpu", spec.get("agent_cpu_limit")),
                    ("memory", spec.get("agent_memory_limit")),
                ) if v} or None,
            )

        sse_port = int(spec.get("sse_port") or 8080)
        container_port = int(spec.get("container_port") or sse_port)
        ports = [c.V1ContainerPort(name="sse", container_port=sse_port)]
        if container_port != sse_port:
            ports.append(c.V1ContainerPort(name="http", container_port=container_port))

        # AgentServer 固定约定：SSE 端口提供 GET /health（场景 N）
        probe = c.V1Probe(
            http_get=c.V1HTTPGetAction(path="/health", port=sse_port),
            initial_delay_seconds=int(spec.get("readiness_initial_delay") or 5),
            period_seconds=int(spec.get("readiness_period") or 5),
        )

        container = c.V1Container(
            name=spec.get("container_name") or "agent",
            image=spec.get("agent_image") or "",
            image_pull_policy=spec.get("image_pull_policy") or "IfNotPresent",
            ports=ports,
            volume_mounts=mounts or None,
            resources=resources,
            readiness_probe=probe,
        )
        return c.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=c.V1ObjectMeta(name=pod_id, namespace=spec.get("namespace")
                                    or self.default_namespace, labels=labels),
            spec=c.V1PodSpec(containers=[container], restart_policy="Always",
                             volumes=volumes or None),
        )

    async def _wait_ready(self, pod_id: str, namespace: str,
                          timeout: float, poll: float) -> PodDeployInfo:
        started = asyncio.get_running_loop().time()
        deadline = started + timeout
        next_progress_at = started + WAIT_READY_PROGRESS_SEC
        last_reason = ""
        while True:
            info = await self._read(pod_id, namespace)
            now = asyncio.get_running_loop().time()
            if info is None:
                raise DeployFailed(f"pod {pod_id} disappeared during deploy")
            if info.phase in ("Failed", "Succeeded"):
                logger.warning(
                    "k8s pod terminal phase during deploy: name=%s phase=%s "
                    "reason=%s waited_s=%.1f",
                    pod_id, info.phase, info.reason, now - started,
                )
                raise DeployFailed(
                    f"pod {pod_id} terminal phase {info.phase}: {info.reason}"
                )
            if info.ready and info.pod_ip:
                logger.info("k8s pod ready: name=%s pod_ip=%s waited_s=%.1f",
                            pod_id, info.pod_ip, now - started)
                return PodDeployInfo(pod_id=pod_id, namespace=namespace,
                                     pod_ip=info.pod_ip)
            last_reason = info.reason or info.phase
            if now >= deadline:
                logger.warning(
                    "k8s pod not Ready in time: name=%s timeout=%ss "
                    "phase=%s reason=%s",
                    pod_id, timeout, info.phase, last_reason,
                )
                raise DeployFailed(
                    f"pod {pod_id} not Ready within {timeout}s "
                    f"(phase={info.phase!r}, reason={last_reason!r})"
                )
            if now >= next_progress_at:
                # 最长 300s 的等待不留日志空白：周期性进度行（镜像拉取慢等可见）
                logger.info(
                    "k8s wait_ready progress: name=%s elapsed_s=%.0f "
                    "phase=%s reason=%s",
                    pod_id, now - started, info.phase, last_reason,
                )
                next_progress_at = now + WAIT_READY_PROGRESS_SEC
            await asyncio.sleep(poll)

    # -------------------------------------------------------------- 查询 / 删除

    async def _read(self, pod_id: str, namespace: str) -> PodInfo | None:
        try:
            pod = await self._core.read_namespaced_pod(name=pod_id, namespace=namespace)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            raise
        return _to_pod_info(pod)

    async def get_pod(self, pod_id: str, namespace: str) -> PodInfo | None:
        if not self._loaded:
            await self.start()
        t0 = time.monotonic()
        result = await self._read(pod_id, namespace)
        logger.debug("k8s get_pod: name=%s found=%s duration_ms=%.1f",
                     pod_id, result is not None, (time.monotonic() - t0) * 1000)
        return result

    async def list_pods(self, namespace: str, label_selector: str) -> list[PodInfo]:
        if not self._loaded:
            await self.start()
        t0 = time.monotonic()
        result = await self._core.list_namespaced_pod(
            namespace=namespace, label_selector=label_selector
        )
        pods = [_to_pod_info(item) for item in result.items]
        logger.debug("k8s list_pods: namespace=%s selector=%s count=%d duration_ms=%.1f",
                     namespace, label_selector, len(pods),
                     (time.monotonic() - t0) * 1000)
        return pods

    async def delete(self, pod_id: str, namespace: str) -> str:
        if not self._loaded:
            await self.start()
        c = self._client
        t0 = time.monotonic()
        try:
            await self._core.delete_namespaced_pod(
                name=pod_id, namespace=namespace,
                body=c.V1DeleteOptions(grace_period_seconds=0),
            )
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise DeployFailed(f"k8s delete pod {pod_id} failed: {exc}") from exc
            logger.info("k8s pod already absent: name=%s", pod_id)
        logger.debug("k8s delete_pod: name=%s duration_ms=%.1f",
                     pod_id, (time.monotonic() - t0) * 1000)
        return pod_id


def _to_pod_info(pod: Any) -> PodInfo:
    """V1Pod → PodInfo（归一化 phase）。"""
    meta = pod.metadata
    status = pod.status or None
    phase = (status.phase if status else "") or ""
    deletion = meta.deletion_timestamp is not None
    waiting_reasons: list[str] = []
    for cs in (status.container_statuses or []) if status else []:
        waiting = getattr(cs.state, "waiting", None) if cs.state else None
        if waiting and getattr(waiting, "reason", None):
            waiting_reasons.append(waiting.reason)
    ready = bool(status) and any(
        c.type == "Ready" and c.status == "True" for c in (status.conditions or [])
    )
    return PodInfo(
        pod_id=meta.name,
        namespace=meta.namespace,
        phase=normalize_phase(phase, deletion, waiting_reasons),
        ready=ready,
        pod_ip=(status.pod_ip if status else "") or "",
        labels=dict(meta.labels or {}),
        reason="; ".join(waiting_reasons),
    )


# ---------------------------------------------------------------- Fake（local / 单测）


class FakeK8sPodClient(K8sPodClient):
    """进程内假集群：deploy 立即 Ready；状态/健康可编程（单测/本地联调用）。

    - ``unready_pods`` / ``dead_pods`` / ``unhealthy_pods``：pod_id 集合，模拟
      NotReady / 判死状态 / SSE 健康探测失败（场景 N）。
    - ``deploy_failures``：连续 deploy 失败次数（模拟 DeployFailed 分支）。
    """

    def __init__(self, default_namespace: str = "default") -> None:
        self.default_namespace = default_namespace
        self.pods: dict[tuple[str, str], PodInfo] = {}       # (namespace, pod_id) → info
        self.unready_pods: set[str] = set()
        self.dead_pods: set[str] = set()
        self.unhealthy_pods: set[str] = set()
        self.deploy_failures = 0
        self.deleted: list[str] = []
        self._ip_counter = 0

    async def deploy(self, pod_spec: dict[str, Any]) -> PodDeployInfo:
        if self.deploy_failures > 0:
            self.deploy_failures -= 1
            raise DeployFailed("simulated deploy failure")
        namespace = pod_spec.get("namespace") or self.default_namespace
        pod_id = f"{pod_spec.get('pod_name') or 'agentserver'}-{_random_suffix(8)}"
        self._ip_counter += 1
        pod_ip = f"10.42.{self._ip_counter // 250}.{self._ip_counter % 250 + 1}"
        self.pods[(namespace, pod_id)] = PodInfo(
            pod_id=pod_id, namespace=namespace, phase="Running", ready=True,
            pod_ip=pod_ip,
            labels={POD_LABEL_KEY: POD_LABEL_VALUE, "app": pod_id},
        )
        return PodDeployInfo(pod_id=pod_id, namespace=namespace, pod_ip=pod_ip)

    async def delete(self, pod_id: str, namespace: str) -> str:
        self.pods.pop((namespace, pod_id), None)
        self.deleted.append(pod_id)
        return pod_id

    async def get_pod(self, pod_id: str, namespace: str) -> PodInfo | None:
        info = self.pods.get((namespace, pod_id))
        if info is None:
            return None
        return self._effective(info)

    async def list_pods(self, namespace: str, label_selector: str) -> list[PodInfo]:
        return [
            self._effective(info)
            for (ns, _), info in self.pods.items()
            if ns == namespace and _matches_selector(info.labels, label_selector)
        ]

    def _effective(self, info: PodInfo) -> PodInfo:
        """叠加可编程状态（unready / dead）。"""
        if info.pod_id in self.dead_pods:
            return PodInfo(**{**info.__dict__, "phase": "Failed", "ready": False})
        if info.pod_id in self.unready_pods:
            return PodInfo(**{**info.__dict__, "ready": False})
        return info

    async def probe_health(self, pod_ip: str, sse_port: int) -> bool:
        return pod_ip not in self.unhealthy_pods


def _matches_selector(labels: dict[str, str] | None, selector: str) -> bool:
    if not selector:
        return True
    labels = labels or {}
    for term in selector.split(","):
        key, _, value = term.partition("=")
        if labels.get(key.strip()) != value.strip():
            return False
    return True
