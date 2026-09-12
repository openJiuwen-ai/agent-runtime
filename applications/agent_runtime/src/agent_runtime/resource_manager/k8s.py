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
import re
import string
import time
from typing import Any

import httpx

from ..containers import (
    MAIN_ROLE,
    SIDECAR_ROLE,
    find_container_conflict,
    main_sse_port,
    normalize_pod_spec,
)
from ..errors import DeployFailed
from ..mounts import normalize_mounts
from .models import POD_LABEL_KEY, POD_LABEL_VALUE, PodDeployInfo, PodInfo

logger = logging.getLogger("agent_runtime.resource_manager")

DEFAULT_READY_TIMEOUT = 300      # deploy 等 Ready 超时（秒）
DEFAULT_READY_POLL_INTERVAL = 2  # 就绪轮询间隔（秒）
# 单次 K8s API 调用超时（秒）。kubernetes_asyncio 不传 _request_timeout 时
# aiohttp ClientTimeout 全 None（连库默认都覆盖掉），API server/网络挂起会
# 无限悬挂并逐级拖死 deploy/get/delete 与上层 HTTP route——所有调用必须带上界。
CREATE_TIMEOUT = 30   # create（建 Pod 载荷重，最宽的读类上界）
READ_TIMEOUT = 10     # 单 Pod read（_wait_ready 轮询间隔 2s，10s 充裕）
LIST_TIMEOUT = 15     # namespace 级 list
DELETE_TIMEOUT = 60   # delete（含驱逐收敛，物理操作最宽）
WAIT_READY_PROGRESS_SEC = 30    # _wait_ready 进度行间隔（最长 300s 不留空白）


def _random_suffix(length: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


_HOSTPATH_NAME_RE = re.compile(r"[^a-z0-9-]+")


def _scoped_volume_name(prefix: str, name: str, idx: int, mount_idx: int) -> str:
    """容器挂载卷名:{prefix} 前缀 + 容器名净化 + 双索引后缀,防同 Pod 多容器
    多挂载撞名(沿老 SDK K8sServiceHandler 约定);DNS-1123,整体 ≤63。
    前缀:hp-(hostPath)/cm-(ConfigMap)/pvc-(PVC)/nfs-(NFS 共享卷)。"""
    sanitized = _HOSTPATH_NAME_RE.sub("-", (name or "").lower()).strip("-") or f"c{idx}"
    suffix = f"-{idx}-{mount_idx}"
    return f"{prefix}-{sanitized[:63 - len(prefix) - 1 - len(suffix)]}{suffix}"


def _host_path_volume_name(name: str, idx: int, mount_idx: int) -> str:
    """hostPath 卷名(兼容旧名,等同 _scoped_volume_name('hp', ...))。"""
    return _scoped_volume_name("hp", name, idx, mount_idx)


def _render_volume_mounts(
        c: Any, owner: str, idx: int, *,
        host_path: list[dict[str, Any]] | None = None,
        config_map: list[dict[str, Any]] | None = None,
        pvc: list[dict[str, Any]] | None = None,
        nfs: list[dict[str, Any]] | None = None,
        hp_seen: dict[tuple[str, Any], str] | None = None,
        cm_seen: dict[tuple, str] | None = None,
        pvc_seen: dict[str, str] | None = None,
        nfs_seen: dict[tuple[str, str], str] | None = None,
) -> tuple[list[Any], list[Any]]:
    """规范形挂载列表 → (Pod 级 volumes, 容器 volume_mounts)。

    规范形由 mounts.py 校验/归一(RM 侧 normalize 兜底脏缓存);owner=容器名、
    idx=容器序号(sidecar 从 0,主容器固定 0,容器名唯一保证卷名不撞)。
    pvc_seen: 跨容器共享的 claim→卷名登记簿;同 claim 的 PVC 只建一个卷,
    主容器与 sidecar 的 volumeMounts 都引用它(对齐 gateway 写法,防 kubelet
    挂第二个同 claim 卷时死锁/超时)。None=不做去重(兼容单容器调用)。
    nfs_seen: 同款登记簿,键 = (server, path)——同一 NFS 共享跨容器只建
    一个卷,主容器与 sidecar 复挂同一共享源时复用卷名。
    hp_seen: 同款登记簿,键 = (host_path, host_path_type)——同一 hostPath
    跨容器只建一个卷(挂载的 mount_path/read_only 是容器侧属性不影响共享);
    None=不做去重(兼容单容器调用)。
    cm_seen: 同款登记簿,键 = (config_map_name, items 键值对元组)——items
    属于卷定义(决定卷内容),同名不同 items 不共享。
    """
    volumes: list[Any] = []
    mounts: list[Any] = []
    for prefix, mlist in (("hp", host_path), ("cm", config_map),
                          ("pvc", pvc), ("nfs", nfs)):
        for mi, m in enumerate(mlist or []):
            volume_name = _scoped_volume_name(prefix, owner, idx, mi)
            if prefix == "hp":
                # 同一 hostPath(同 path+type)跨容器只建一个卷,主+sidecar
                # 都引用它(对齐 nfs_seen/pvc_seen 写法)
                share = (m["host_path"], m["host_path_type"])
                if hp_seen is not None and share in hp_seen:
                    volume_name = hp_seen[share]
                else:
                    volumes.append(c.V1Volume(
                        name=volume_name,
                        host_path=c.V1HostPathVolumeSource(
                            path=m["host_path"], type=m["host_path_type"]),
                    ))
                    if hp_seen is not None:
                        hp_seen[share] = volume_name
                mounts.append(c.V1VolumeMount(
                    name=volume_name, mount_path=m["mount_path"],
                    read_only=m["read_only"],
                ))
            elif prefix == "cm":
                items = ([c.V1KeyToPath(key=e["key"], path=e["path"])
                          for e in m["items"]] if m["items"] else None)
                # items 属于卷定义(决定卷内容):同名不同 items 不共享
                share = (m["config_map_name"],
                         tuple((e["key"], e["path"])
                               for e in (m["items"] or [])))
                if cm_seen is not None and share in cm_seen:
                    volume_name = cm_seen[share]
                else:
                    volumes.append(c.V1Volume(
                        name=volume_name,
                        config_map=c.V1ConfigMapVolumeSource(
                            name=m["config_map_name"], items=items),
                    ))
                    if cm_seen is not None:
                        cm_seen[share] = volume_name
                mounts.append(c.V1VolumeMount(
                    name=volume_name, mount_path=m["mount_path"],
                    sub_path=m["sub_path"], read_only=m["read_only"],
                ))
            elif prefix == "nfs":
                # 同一 NFS 共享(同 server+path)跨容器只建一个卷,主+sidecar
                # 都引用它(对齐 pvc_seen 写法)
                share = (m["server"], m["path"] or "/")
                if nfs_seen is not None and share in nfs_seen:
                    volume_name = nfs_seen[share]
                else:
                    volumes.append(c.V1Volume(
                        name=volume_name,
                        nfs=c.V1NFSVolumeSource(
                            server=m["server"], path=m["path"] or "/"),
                    ))
                    if nfs_seen is not None:
                        nfs_seen[share] = volume_name
                mounts.append(c.V1VolumeMount(
                    name=volume_name, mount_path=m["mount_path"],
                    read_only=m["read_only"],
                ))
            else:  # pvc: 同 claim 跨容器只建一个共享卷,主+sidecar 都引用它
                claim = m["claim_name"]
                if pvc_seen is not None and claim in pvc_seen:
                    volume_name = pvc_seen[claim]
                else:
                    volume_name = _scoped_volume_name("pvc", owner, idx, mi)
                    volumes.append(c.V1Volume(
                        name=volume_name,
                        persistent_volume_claim=(
                            c.V1PersistentVolumeClaimVolumeSource(
                                claim_name=claim, read_only=m["read_only"])),
                    ))
                    if pvc_seen is not None:
                        pvc_seen[claim] = volume_name
                mounts.append(c.V1VolumeMount(
                    name=volume_name, mount_path=m["mount_path"],
                    read_only=m["read_only"],
                ))
    return volumes, mounts


def _render_env_from(c: Any, env_from: list[dict[str, Any]] | None) -> list[Any] | None:
    """内部 envFrom 规范形 → V1EnvFromSource 列表(主/sidecar 共用)。

    脏缓存防御(pod_spec 可能来自 Redis 旧缓存/手改):坏项跳过不抛;
    值 None/空 → None(容器不设 envFrom,与历史行为逐字节一致)。
    """
    if not env_from:
        return None
    out: list[Any] = []
    for item in env_from:
        if not isinstance(item, dict):
            continue
        secret_ref = item.get("secret_ref")
        config_map_ref = item.get("config_map_ref")
        if not (isinstance(secret_ref, dict) ^ isinstance(config_map_ref, dict)):
            continue
        prefix = item.get("prefix")
        kwargs: dict[str, Any] = {
            "prefix": prefix if isinstance(prefix, str) else None}
        if isinstance(secret_ref, dict):
            name = secret_ref.get("name")
            if not isinstance(name, str) or not name:
                continue
            kwargs["secret_ref"] = c.V1SecretEnvSource(
                name=name, optional=bool(secret_ref.get("optional")))
        else:
            name = config_map_ref.get("name")
            if not isinstance(name, str) or not name:
                continue
            kwargs["config_map_ref"] = c.V1ConfigMapEnvSource(
                name=name, optional=bool(config_map_ref.get("optional")))
        out.append(c.V1EnvFromSource(**kwargs))
    return out or None


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

    async def probe_health(self, pod_ip: str, sse_port: int,
                           health_path: str = "/health") -> bool:
        """场景 N：探测 AgentServer 健康端点 GET http://{pod_ip}:{sse_port}{health_path}。"""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"http://{pod_ip}:{sse_port}{health_path or '/health'}")
                if response.status_code != 200:
                    logger.debug("health probe non-200: ip=%s port=%s path=%s status=%s",
                                 pod_ip, sse_port, health_path, response.status_code)
                return response.status_code == 200
        except Exception as exc:  # noqa: BLE001 - 探测失败即不健康
            # 失败原因留痕（调用方 sweeper 有同节奏 WARNING，这里 DEBUG 补细节）
            logger.debug("health probe error: ip=%s port=%s path=%s error=%s: %s",
                         pod_ip, sse_port, health_path,
                         type(exc).__name__, exc)
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
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        # 并发 deploy 首开窗口（多请求同时冷启动）：锁内双检保证只建一个
        # ApiClient——无锁时两路各建一个，其一泄漏（连接/fd 不 close）。
        async with self._lifecycle_lock:
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
        # 锁内只做引用摘除（快照 + 置空 + 复位 _loaded），网络收尾放锁外——
        # 持锁等 close() 会饿死并发 start()。在飞调用持旧引用继续，底层连接
        # 池被关时由各调用点的快照 None 检查/异常分支归一为 DeployFailed。
        async with self._lifecycle_lock:
            api_client = self._api_client
            self._core = self._api_client = self._client = None
            self._loaded = False
        if api_client is not None:
            await api_client.close()

    # -------------------------------------------------------------- deploy

    async def deploy(self, pod_spec: dict[str, Any]) -> PodDeployInfo:
        """create + wait Ready。契约：**任何失败/取消路径不得留下已建物理 Pod**。

        create 成功后 `_wait_ready` 超时/终态/取消 → 先 best-effort 删除该
        Pod 再抛（DeployFailed 携带 pod_id/namespace 供上层兜底）；不删的话
        该 Pod 不在 Redis `pods:all`（未 REGISTER），watch/reconcile 只做
        Redis→K8s 单向对账，孤儿将无人认领、无上界累积。
        """
        await self.start()
        core = self._core
        if core is None:  # start() 后仍为 None = 与 close() 的关停竞态
            raise DeployFailed("k8s client closed")
        namespace = pod_spec.get("namespace") or self.default_namespace
        timeout = int(pod_spec.get("ready_timeout") or DEFAULT_READY_TIMEOUT)
        poll = float(pod_spec.get("ready_poll_interval") or DEFAULT_READY_POLL_INTERVAL)

        for _ in range(3):  # 409 名字冲突 → 重命名重试（至多 3 次）
            pod_id = f"{pod_spec.get('pod_name') or 'agentserver'}-{_random_suffix(10)}-{_random_suffix(5)}"
            body = self._build_pod_body(pod_id, pod_spec)
            logger.info("k8s create pod: name=%s namespace=%s image=%s",
                        pod_id, namespace,
                        (pod_spec.get("main_container") or {}).get("image"))
            try:
                await core.create_namespaced_pod(
                    namespace=namespace, body=body, _request_timeout=CREATE_TIMEOUT)
            except Exception as exc:
                if getattr(exc, "status", None) == 409:
                    logger.warning("k8s pod name conflict, retrying: name=%s", pod_id)
                    continue
                raise DeployFailed(f"k8s create pod failed: {exc}") from exc
            try:
                return await self._wait_ready(pod_id, namespace, timeout, poll)
            except BaseException as exc:  # noqa: BLE001 - 物理清理红线含取消路径
                for attr, val in (("pod_id", pod_id), ("namespace", namespace)):
                    try:
                        setattr(exc, attr, val)
                    except Exception:  # noqa: BLE001 - 部分异常不可设属性
                        pass
                try:
                    await self.delete(pod_id, namespace)
                except Exception:  # noqa: BLE001 - 清理失败不掩盖原始异常
                    logger.warning(
                        "orphan pod cleanup after failed deploy failed: "
                        "name=%s namespace=%s", pod_id, namespace,
                    )
                raise
        raise DeployFailed("k8s create pod failed: name conflicts exhausted")

    # -------------------------------------------------------------- 容器渲染(主/sidecar 统一)

    @staticmethod
    def _build_security_context(c: Any, secctx: dict[str, Any]) -> Any | None:
        """sidecar 安全上下文(移植老 SDK _build_security_context 精简版):
        privileged/caps/seccomp/run_as_;apparmor 走 Pod annotation(调用方收集)。"""
        capabilities = None
        if secctx.get("capabilities_add") or secctx.get("capabilities_drop"):
            capabilities = c.V1Capabilities(
                add=secctx.get("capabilities_add") or None,
                drop=secctx.get("capabilities_drop") or None,
            )
        kwargs = {
            "privileged": True if secctx.get("privileged") else None,
            "capabilities": capabilities,
            "seccomp_profile": (c.V1SeccompProfile(type="Unconfined")
                                if secctx.get("seccomp_unconfined") else None),
            "run_as_user": secctx.get("run_as_user"),
            "run_as_group": secctx.get("run_as_group"),
        }
        if all(value is None for value in kwargs.values()):
            return None
        return c.V1SecurityContext(**kwargs)

    def _build_container(
            self, c: Any, cont: dict[str, Any], *, role: str, idx: int,
            pod_id: str = "",
            hp_seen: dict[tuple[str, Any], str] | None = None,
            cm_seen: dict[tuple, str] | None = None,
            pvc_seen: dict[str, str] | None = None,
            nfs_seen: dict[tuple[str, str], str] | None = None,
    ) -> tuple[Any, list[Any], dict[str, str]]:
        """单个容器(canonical,见 containers.py)→ (V1Container, 挂载卷, Pod annotation)。

        主/sidecar 统一渲染器;role 差异收敛为两处:ports 有名(sse/http)vs
        无名纯声明、探针恒 httpGet 打 sse 端口且无 timeout vs 可选 tcp/http
        带 timeout。securityContext/command/args/挂载四族/env/envFrom/
        resources 主/sidecar 一致渲染(apparmor 走 Pod annotation,role 无关;
        security_context 全默认时传 None = 走镜像默认)。挂载四族
        (hp/cm/pvc/nfs)主/sidecar 一致(_render_volume_mounts),pvc_seen/
        nfs_seen 跨容器共享同 claim/同 server+path 的卷(防 kubelet 挂第二
        个同源卷死锁)。
        """
        is_main = role == MAIN_ROLE
        volumes, mounts = _render_volume_mounts(
            c, cont["name"], idx,
            host_path=normalize_mounts(cont.get("host_path_mounts"),
                                       "host_path_mounts"),
            config_map=normalize_mounts(cont.get("configmap_mounts"),
                                        "configmap_mounts"),
            pvc=normalize_mounts(cont.get("pvc_mounts"), "pvc_mounts"),
            nfs=normalize_mounts(cont.get("nfs_mounts"), "nfs_mounts"),
            hp_seen=hp_seen,
            cm_seen=cm_seen,
            pvc_seen=pvc_seen,
            nfs_seen=nfs_seen,
        )

        resources = None
        res = cont.get("resources") or {}
        if any(res.get(f) for f in ("cpu_request", "memory_request",
                                    "cpu_limit", "memory_limit")):
            resources = c.V1ResourceRequirements(
                requests={k: v for k, v in (
                    ("cpu", res.get("cpu_request")),
                    ("memory", res.get("memory_request")),
                ) if v} or None,
                limits={k: v for k, v in (
                    ("cpu", res.get("cpu_limit")),
                    ("memory", res.get("memory_limit")),
                ) if v} or None,
            )

        ports = None
        if is_main:
            sse_port = main_sse_port(cont)
            ports = [c.V1ContainerPort(name="sse", container_port=sse_port)]
            for p in cont.get("ports") or []:
                if (p.get("name") == "http"
                        and p.get("container_port") != sse_port):
                    ports.append(c.V1ContainerPort(
                        name="http", container_port=p["container_port"]))
        elif cont.get("ports"):
            # 端口纯声明性(无名,消灭端口名撞号类 bug):sidecar 只被同 Pod
            # 127.0.0.1 访问,不进 Service,gateway 仍直连 Pod IP 的 sse 端口
            ports = [c.V1ContainerPort(
                container_port=cont["ports"][0]["container_port"])]

        probe = None
        probe_spec = cont.get("readiness_probe") or {}
        if is_main:
            # AgentServer 固定约定:SSE 端口提供健康端点(默认 /health,模板
            # 可覆盖——真 AgentServer HTTP 入口为 /api/v1/health);无 timeout
            probe = c.V1Probe(
                http_get=c.V1HTTPGetAction(
                    path=probe_spec.get("path") or "/health",
                    port=main_sse_port(cont)),
                initial_delay_seconds=int(probe_spec.get("initial_delay") or 5),
                period_seconds=int(probe_spec.get("period") or 5),
            )
        elif probe_spec.get("probe_type"):
            common = {
                "initial_delay_seconds": int(
                    probe_spec.get("initial_delay") or 5),
                "period_seconds": int(probe_spec.get("period") or 10),
                "timeout_seconds": int(probe_spec.get("timeout") or 3),
            }
            port = (cont["ports"][0]["container_port"]
                    if cont.get("ports") else None)
            if port is not None:
                if probe_spec["probe_type"] == "tcp":
                    probe = c.V1Probe(
                        tcp_socket=c.V1TCPSocketAction(port=port), **common)
                else:
                    probe = c.V1Probe(
                        http_get=c.V1HTTPGetAction(
                            path=probe_spec.get("path") or "/health",
                            port=port),
                        **common,
                    )

        env = [
            c.V1EnvVar(name=str(k), value=str(v))
            for k, v in (cont.get("env") or {}).items()
        ] or None

        # 启动命令/参数覆盖(主/sidecar 一致;缺省走镜像 ENTRYPOINT/CMD)
        cmd_kwargs: dict[str, Any] = {}
        if cont.get("command"):
            cmd_kwargs["command"] = [str(x) for x in cont["command"]]
        if cont.get("args"):
            cmd_kwargs["args"] = [str(x) for x in cont["args"]]

        container_kwargs: dict[str, Any] = {
            "name": cont["name"],
            "image": cont.get("image") or "",
            "image_pull_policy": (cont.get("image_pull_policy")
                                  or "IfNotPresent"),
            "ports": ports,
            "env": env,
            "env_from": _render_env_from(c, cont.get("env_from")),
            "volume_mounts": mounts or None,
            "resources": resources,
            "readiness_probe": probe,
        }
        secctx = cont.get("security_context") or {}
        # securityContext 主/sidecar 全量一致渲染(决策 B:主容器特权面放开;
        # 全默认 → None,走镜像默认)
        container_kwargs["security_context"] = self._build_security_context(
            c, secctx)
        container = c.V1Container(**container_kwargs, **cmd_kwargs)
        # apparmor unconfined 只能以 Pod annotation 表达(老 SDK 同款)
        annotations = ({f"container.apparmor.security.beta.kubernetes.io/{cont['name']}":
                        "unconfined"} if secctx.get("apparmor_unconfined") else {})
        return container, volumes, annotations

    def _build_pod_body(self, pod_id: str, spec: dict[str, Any]) -> Any:
        c = self._client
        labels = {POD_LABEL_KEY: POD_LABEL_VALUE, "app": pod_id}
        # pod_spec 可能来自 Redis pod_spec_json 缓存(旧版本写入/手改):
        # normalize 补缺省键;shape 探测(缺 main_container = 旧扁平缓存)
        # fail-fast,防渲染出空镜像 Pod
        spec = normalize_pod_spec(spec)
        main = spec.get("main_container")
        if not isinstance(main, dict):
            raise DeployFailed(
                "pod spec has no main_container (legacy flat-form cache "
                "written before the unified container canonical? re-push "
                "config_sync/config_refresh first)")
        sidecars = spec.get("sidecars") or []
        # 容器名/端口冲突 fail-fast(防 Pod 建出来 agent 经 127.0.0.1 连错进程)
        conflict = find_container_conflict(main, sidecars)
        if conflict:
            raise DeployFailed(f"pod spec containers invalid: {conflict}")

        # 跨容器共享卷登记簿:同源只建一个 Pod 级卷,主+sidecar 复用卷名
        hp_seen: dict[tuple[str, Any], str] = {}  # 同 (path, type) 的 hostPath
        cm_seen: dict[tuple, str] = {}  # 同 (name, items) 的 ConfigMap
        pvc_seen: dict[str, str] = {}  # 同 claim 的 PVC
        nfs_seen: dict[tuple[str, str], str] = {}  # 同 server+path 的 NFS
        container, volumes, annotations = self._build_container(
            c, main, role=MAIN_ROLE, idx=0, pod_id=pod_id, hp_seen=hp_seen,
            cm_seen=cm_seen, pvc_seen=pvc_seen, nfs_seen=nfs_seen)
        # ---- sidecar 容器(通用机制,canonical 见 containers.py;无 sidecars
        # 时零改动:annotations=None、containers=[main] 与历史逐字节一致)
        containers: list[Any] = [container]
        for idx, sc in enumerate(sidecars):
            sc_container, sc_volumes, sc_annotations = (
                self._build_container(c, sc, role=SIDECAR_ROLE, idx=idx,
                                      pod_id=pod_id, hp_seen=hp_seen,
                                      cm_seen=cm_seen, pvc_seen=pvc_seen,
                                      nfs_seen=nfs_seen))
            containers.append(sc_container)
            volumes.extend(sc_volumes)
            annotations.update(sc_annotations)

        # Pod 级 securityContext.fsGroup(模板级 wire 键 fsGroup;None = 不设,
        # kubelet 不做卷属主修正——NFS 卷属主问题的官方修法)
        pod_security_context = None
        if spec.get("fs_group") is not None:
            pod_security_context = c.V1PodSecurityContext(
                fs_group=int(spec["fs_group"]))

        return c.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=c.V1ObjectMeta(name=pod_id, namespace=spec.get("namespace")
                                    or self.default_namespace, labels=labels,
                                    annotations=annotations or None),
            spec=c.V1PodSpec(containers=containers,
                             restart_policy="Always",
                             volumes=volumes or None,
                             node_name=(spec.get("node_name") or None),
                             security_context=pod_security_context),
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
        core = self._core
        if core is None:  # close() 摘走引用（停机竞态），不裸抛 AttributeError
            raise DeployFailed("k8s client closed")
        try:
            pod = await core.read_namespaced_pod(
                name=pod_id, namespace=namespace, _request_timeout=READ_TIMEOUT)
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
        core = self._core
        if core is None:
            raise DeployFailed("k8s client closed")
        t0 = time.monotonic()
        result = await core.list_namespaced_pod(
            namespace=namespace, label_selector=label_selector,
            _request_timeout=LIST_TIMEOUT,
        )
        pods = [_to_pod_info(item) for item in result.items]
        logger.debug("k8s list_pods: namespace=%s selector=%s count=%d duration_ms=%.1f",
                     namespace, label_selector, len(pods),
                     (time.monotonic() - t0) * 1000)
        return pods

    async def delete(self, pod_id: str, namespace: str) -> str:
        if not self._loaded:
            await self.start()
        core, c = self._core, self._client
        if core is None or c is None:
            raise DeployFailed("k8s client closed")
        t0 = time.monotonic()
        try:
            await core.delete_namespaced_pod(
                name=pod_id, namespace=namespace,
                body=c.V1DeleteOptions(grace_period_seconds=0),
                _request_timeout=DELETE_TIMEOUT,
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
    - ``deploy_failures``：连续 deploy 失败次数（create 前失败，无物理残留）。
    - ``fail_after_create``：create 成功但永不 Ready 的次数——Pod 留在集群、
      DeployFailed 携带 pod_id/namespace（真 K8s 的超时/取消形态，考验上层
      「失败路径不留孤儿」的兜底删除）。
    - ``delete_failures``：连续 delete 失败次数（非 404 形态，考验 PURGE 的
      delete 门槛——失败时本拍不得 PURGE，留待下拍重试）。
    """

    def __init__(self, default_namespace: str = "default") -> None:
        self.default_namespace = default_namespace
        self.pods: dict[tuple[str, str], PodInfo] = {}       # (namespace, pod_id) → info
        self.unready_pods: set[str] = set()
        self.dead_pods: set[str] = set()
        self.unhealthy_pods: set[str] = set()
        self.deploy_failures = 0
        self.fail_after_create = 0
        self.delete_failures = 0
        self.deleted: list[str] = []
        self.deployed_specs: list[dict[str, Any]] = []       # deploy 收到的 pod_spec 录制(断言用)
        self._ip_counter = 0

    async def deploy(self, pod_spec: dict[str, Any]) -> PodDeployInfo:
        self.deployed_specs.append(dict(pod_spec))
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
        if self.fail_after_create > 0:
            self.fail_after_create -= 1
            self.unready_pods.add(pod_id)
            exc = DeployFailed(
                f"simulated: pod {pod_id} created but never Ready within timeout"
            )
            exc.pod_id, exc.namespace = pod_id, namespace
            raise exc
        return PodDeployInfo(pod_id=pod_id, namespace=namespace, pod_ip=pod_ip)

    async def delete(self, pod_id: str, namespace: str) -> str:
        if self.delete_failures > 0:
            self.delete_failures -= 1
            raise DeployFailed(f"simulated delete failure: {pod_id}")
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

    async def probe_health(self, pod_ip: str, sse_port: int,
                           health_path: str = "/health") -> bool:
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
