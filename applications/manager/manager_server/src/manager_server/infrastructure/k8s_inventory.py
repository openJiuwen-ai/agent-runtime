# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager 侧只读 K8s 库存：列 Pod + kubelet ``/stats/summary`` 用量。

不依赖 metrics-server；用量来自节点 kubelet Summary（经 API Server
``nodes/proxy``）。凭证优先级：
1. ``MANAGER_DEPLOY_KUBECONFIG``（k8s 部署卷）
2. ``MANAGER_DEPLOY_TOKEN`` + ``MANAGER_DEPLOY_API_SERVER``（OpenShift token 卷）
3. in-cluster ServiceAccount

Token 路径默认校验证书：
- ``MANAGER_DEPLOY_SSL_CA_CERT``：自定义 CA 文件
- 未配置时尝试 in-cluster ``.../serviceaccount/ca.crt``
- 仅当 ``MANAGER_DEPLOY_INSECURE_SKIP_TLS_VERIFY=true`` 时跳过校验
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from manager_server.infrastructure.logger import get_logger

_log = get_logger(__name__)

ComponentType = Literal["gateway", "runtime", "agentserver"]

COMPONENT_LABEL_SELECTORS: dict[ComponentType, str] = {
    "gateway": "app=jiuwenswarm-gateway",
    "runtime": "app=jiuwenswarm-agent-runtime",
    # 静态 StatefulSet 与 Runtime 动态 Pod 均打 jiuwenswarm-component=agentserver
    "agentserver": "jiuwenswarm-component=agentserver",
}

_LIST_TIMEOUT = 8.0
_SUMMARY_TIMEOUT = 10.0
_SUMMARY_CACHE_TTL_S = 20.0
_summary_cache: dict[str, tuple[float, dict[str, tuple[str | None, str | None]]]] = {}


@dataclass(slots=True)
class PodInventoryItem:
    name: str
    phase: str
    ready: bool
    restarts: int
    started_at: str | None = None
    cpu: str | None = None
    memory: str | None = None
    cpu_limit: str | None = None
    memory_limit: str | None = None


@dataclass(slots=True)
class ComponentInventory:
    type: ComponentType
    pods: list[PodInventoryItem] = field(default_factory=list)
    pods_error: str | None = None
    metrics_error: str | None = None


@dataclass(slots=True)
class NamespaceInventory:
    namespace: str
    components: list[ComponentInventory]
    k8s_error: str | None = None


def _parse_quantity_to_millicores(raw: str | None) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("n"):
            return max(0, int(int(text[:-1]) / 1_000_000))
        if text.endswith("u"):
            return max(0, int(int(text[:-1]) / 1_000))
        if text.endswith("m"):
            return max(0, int(text[:-1]))
        if "." in text:
            return max(0, int(float(text) * 1000))
        return max(0, int(text) * 1000)
    except (TypeError, ValueError):
        return None


def _parse_quantity_to_bytes(raw: str | None) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        units = {
            "Ki": 1024,
            "Mi": 1024**2,
            "Gi": 1024**3,
            "Ti": 1024**4,
            "K": 1000,
            "M": 1000**2,
            "G": 1000**3,
            "T": 1000**4,
        }
        for suffix, mult in units.items():
            if text.endswith(suffix):
                return max(0, int(float(text[: -len(suffix)]) * mult))
        if text.endswith("i"):
            return max(0, int(text[:-1]))
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return None


def format_cpu_millicores(raw: str | None) -> str | None:
    millis = _parse_quantity_to_millicores(raw)
    if millis is None:
        return None
    if millis >= 1000 and millis % 1000 == 0:
        return f"{millis // 1000}"
    return f"{millis}m"


def format_memory_mib(raw: str | None) -> str | None:
    nbytes = _parse_quantity_to_bytes(raw)
    if nbytes is None:
        return None
    mib = nbytes / (1024 * 1024)
    if mib >= 1024:
        return f"{mib / 1024:.1f}Gi"
    if mib >= 10:
        return f"{mib:.0f}Mi"
    return f"{mib:.1f}Mi"


def format_cpu_from_nanocores(nano: Any) -> str | None:
    try:
        value = int(nano)
    except (TypeError, ValueError):
        return None
    millis = max(0, value // 1_000_000)
    return format_cpu_millicores(f"{millis}m")


def format_memory_from_bytes(nbytes: Any) -> str | None:
    try:
        value = int(nbytes)
    except (TypeError, ValueError):
        return None
    return format_memory_mib(str(value))


def _pod_ready(pod: Any) -> bool:
    status = getattr(pod, "status", None)
    if status is None:
        return False
    phase = str(getattr(status, "phase", "") or "")
    if phase != "Running":
        return False
    conditions = getattr(status, "conditions", None) or []
    for cond in conditions:
        if str(getattr(cond, "type", "") or "") == "Ready":
            return str(getattr(cond, "status", "") or "").lower() == "true"
    return False


def _pod_restarts(pod: Any) -> int:
    status = getattr(pod, "status", None)
    total = 0
    for cs in getattr(status, "container_statuses", None) or []:
        try:
            total += int(getattr(cs, "restart_count", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _pod_started_at(pod: Any) -> str | None:
    """对应 ``kubectl get pods`` 的 AGE 起点：优先 ``status.startTime``。"""
    from manager_server.infrastructure.utils import iso_datetime

    status = getattr(pod, "status", None)
    start = getattr(status, "start_time", None) or getattr(status, "startTime", None)
    if isinstance(start, str) and start.strip():
        return start.strip()
    if start is not None:
        formatted = iso_datetime(start)
        if formatted:
            return formatted
    meta = getattr(pod, "metadata", None)
    created = getattr(meta, "creation_timestamp", None) or getattr(meta, "creationTimestamp", None)
    if isinstance(created, str) and created.strip():
        return created.strip()
    return iso_datetime(created) if created is not None else None


def _container_resource_raw(pod: Any, resource: str, *, kind: str = "limits") -> str | None:
    """汇总所有普通容器的 limits/requests（与 Pod 级用量对齐）。"""
    spec = getattr(pod, "spec", None)
    values: list[str] = []
    for container in getattr(spec, "containers", None) or []:
        resources = getattr(container, "resources", None)
        bucket = getattr(resources, kind, None) if resources is not None else None
        if bucket is None and isinstance(resources, dict):
            bucket = resources.get(kind)
        bucket = bucket or {}
        raw = None
        if isinstance(bucket, dict) and resource in bucket:
            raw = bucket.get(resource)
        else:
            try:
                raw = bucket[resource]
            except Exception:  # noqa: BLE001
                raw = None
        if raw is not None and str(raw).strip():
            values.append(str(raw).strip())
    if not values:
        return None
    if resource == "cpu":
        total = 0
        ok = False
        for item in values:
            millis = _parse_quantity_to_millicores(item)
            if millis is not None:
                total += millis
                ok = True
        return f"{total}m" if ok else values[0]
    total_b = 0
    ok = False
    for item in values:
        nbytes = _parse_quantity_to_bytes(item)
        if nbytes is not None:
            total_b += nbytes
            ok = True
    return str(total_b) if ok else values[0]


def _container_limit(pod: Any, resource: str) -> str | None:
    return _container_resource_raw(pod, resource, kind="limits")


def _pod_node_name(pod: Any) -> str:
    spec = getattr(pod, "spec", None)
    return str(getattr(spec, "node_name", None) or getattr(spec, "nodeName", None) or "").strip()


_INCLUSTER_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


def _env_flag_true(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_deploy_ssl_ca_cert() -> str | None:
    """Token 建连用的 CA：显式配置优先，否则尝试 in-cluster SA CA。"""
    configured = (os.getenv("MANAGER_DEPLOY_SSL_CA_CERT") or "").strip()
    if configured and os.path.isfile(configured):
        return configured
    if os.path.isfile(_INCLUSTER_CA):
        return _INCLUSTER_CA
    return None


async def _build_api_client():
    from kubernetes_asyncio import client, config
    from kubernetes_asyncio.config.config_exception import ConfigException

    kubeconfig = (os.getenv("MANAGER_DEPLOY_KUBECONFIG") or "").strip()
    token_path = (os.getenv("MANAGER_DEPLOY_TOKEN") or "").strip()
    api_server = (os.getenv("MANAGER_DEPLOY_API_SERVER") or "").strip()

    if kubeconfig and os.path.isfile(kubeconfig):
        await config.load_kube_config(config_file=kubeconfig)
        return client.ApiClient()

    if token_path and os.path.isfile(token_path) and api_server:
        with open(token_path, encoding="utf-8") as handle:
            token = handle.read().strip()
        configuration = client.Configuration()
        configuration.host = api_server
        configuration.api_key = {"authorization": f"Bearer {token}"}
        skip_tls = _env_flag_true("MANAGER_DEPLOY_INSECURE_SKIP_TLS_VERIFY")
        if skip_tls:
            configuration.verify_ssl = False
            _log.warning(
                "k8s_inventory_tls_verify_skipped",
                reason="MANAGER_DEPLOY_INSECURE_SKIP_TLS_VERIFY enabled",
            )
        else:
            configuration.verify_ssl = True
            ca_cert = _resolve_deploy_ssl_ca_cert()
            if ca_cert:
                configuration.ssl_ca_cert = ca_cert
            else:
                _log.warning(
                    "k8s_inventory_tls_ca_missing",
                    hint=(
                        "set MANAGER_DEPLOY_SSL_CA_CERT to the API server CA file, "
                        "or MANAGER_DEPLOY_INSECURE_SKIP_TLS_VERIFY=true only for non-prod"
                    ),
                )
        return client.ApiClient(configuration)

    try:
        config.load_incluster_config()
    except ConfigException as exc:
        raise RuntimeError(
            "kubernetes credentials unavailable "
            "(no deploy kubeconfig/token and not in-cluster)"
        ) from exc
    return client.ApiClient()


async def _list_pods(api_client, *, namespace: str, label_selector: str) -> list[Any]:
    from kubernetes_asyncio import client

    core = client.CoreV1Api(api_client)
    result = await core.list_namespaced_pod(
        namespace=namespace,
        label_selector=label_selector,
        _request_timeout=_LIST_TIMEOUT,
    )
    return list(getattr(result, "items", None) or [])


def _usage_from_summary_pod(item: dict[str, Any]) -> tuple[str | None, str | None]:
    """优先用 pod 级 cpu/memory；缺失时累加 containers。"""
    cpu = item.get("cpu") or {}
    memory = item.get("memory") or {}
    cpu_fmt = format_cpu_from_nanocores(cpu.get("usageNanoCores"))
    mem_fmt = format_memory_from_bytes(
        memory.get("workingSetBytes") or memory.get("usageBytes")
    )
    if cpu_fmt is not None and mem_fmt is not None:
        return cpu_fmt, mem_fmt

    cpu_nano = 0
    mem_bytes = 0
    cpu_ok = False
    mem_ok = False
    for container in item.get("containers") or []:
        c_cpu = (container.get("cpu") or {}).get("usageNanoCores")
        c_mem = (container.get("memory") or {}).get("workingSetBytes") or (
            container.get("memory") or {}
        ).get("usageBytes")
        try:
            if c_cpu is not None:
                cpu_nano += int(c_cpu)
                cpu_ok = True
        except (TypeError, ValueError):
            pass
        try:
            if c_mem is not None:
                mem_bytes += int(c_mem)
                mem_ok = True
        except (TypeError, ValueError):
            pass
    return (
        format_cpu_from_nanocores(cpu_nano) if cpu_ok else cpu_fmt,
        format_memory_from_bytes(mem_bytes) if mem_ok else mem_fmt,
    )


async def _fetch_node_summary_usage(
    api_client, *, node_name: str, namespace: str
) -> tuple[dict[str, tuple[str | None, str | None]], str | None]:
    """拉取节点 summary，返回该 namespace 下 ``{pod_name: (cpu, memory)}``。"""
    import json

    cache_key = f"{node_name}|{namespace}"
    hit = _summary_cache.get(cache_key)
    now = time.monotonic()
    if hit is not None and hit[0] > now:
        return hit[1], None

    from kubernetes_asyncio import client

    core = client.CoreV1Api(api_client)
    try:
        # preload=False：避免客户端把 JSON 误转成单引号 Python repr 字符串
        response = await core.connect_get_node_proxy_with_path(
            name=node_name,
            path="stats/summary",
            _request_timeout=_SUMMARY_TIMEOUT,
            _preload_content=False,
        )
        try:
            body = await response.read()
        finally:
            response.release()
        if isinstance(body, (bytes, bytearray)):
            payload = json.loads(body.decode("utf-8"))
        elif isinstance(body, str):
            payload = json.loads(body)
        elif isinstance(body, dict):
            payload = body
        else:
            payload = json.loads(str(body))
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status", None)
        if status == 403:
            return {}, "kubelet summary forbidden (403)"
        return {}, f"kubelet summary unavailable: {exc}"

    usage: dict[str, tuple[str | None, str | None]] = {}
    for item in (payload or {}).get("pods") or []:
        ref = item.get("podRef") or {}
        if str(ref.get("namespace") or "") != namespace:
            continue
        name = str(ref.get("name") or "").strip()
        if not name:
            continue
        usage[name] = _usage_from_summary_pod(item)

    _summary_cache[cache_key] = (now + _SUMMARY_CACHE_TTL_S, usage)
    return usage, None


async def _usage_for_pods(
    api_client, *, namespace: str, pods: list[Any]
) -> tuple[dict[str, tuple[str | None, str | None]], str | None]:
    """用量：kubelet Summary（经 nodes/proxy），不依赖 metrics-server。"""
    if not pods:
        return {}, None

    node_names: set[str] = set()
    for pod in pods:
        node = _pod_node_name(pod)
        if node:
            node_names.add(node)
    nodes = sorted(node_names)
    if not nodes:
        return {}, None

    merged: dict[str, tuple[str | None, str | None]] = {}
    errors: list[str] = []
    for node in nodes:
        usage, err = await _fetch_node_summary_usage(
            api_client, node_name=node, namespace=namespace
        )
        if err:
            errors.append(f"{node}: {err}")
            continue
        merged.update(usage)
    if merged:
        return merged, ("; ".join(errors) if errors else None)
    return {}, ("; ".join(errors) if errors else "kubelet summary returned no pods")


async def list_component_inventory(namespace: str) -> NamespaceInventory:
    """按组件 label 拉取指定命名空间的 Pod + kubelet Summary 用量。"""
    ns = str(namespace or "").strip() or "default"
    components: list[ComponentInventory] = []

    try:
        api_client = await _build_api_client()
    except Exception as exc:  # noqa: BLE001
        _log.warning("k8s_inventory_client_failed", error=str(exc))
        for ctype in COMPONENT_LABEL_SELECTORS:
            components.append(
                ComponentInventory(type=ctype, pods_error=str(exc), metrics_error=str(exc))
            )
        return NamespaceInventory(namespace=ns, components=components, k8s_error=str(exc))

    try:
        listed: dict[ComponentType, list[Any] | str] = {}
        all_pods: list[Any] = []
        for ctype, selector in COMPONENT_LABEL_SELECTORS.items():
            try:
                pods = await _list_pods(
                    api_client, namespace=ns, label_selector=selector
                )
                listed[ctype] = pods
                all_pods.extend(pods)
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, "status", None)
                listed[ctype] = (
                    f"pods list forbidden ({status})"
                    if status == 403
                    else f"pods list failed: {exc}"
                )

        usage_map, metrics_error = await _usage_for_pods(
            api_client, namespace=ns, pods=all_pods
        )

        for ctype in COMPONENT_LABEL_SELECTORS:
            entry = listed.get(ctype)
            inventory = ComponentInventory(type=ctype, metrics_error=metrics_error)
            if isinstance(entry, str):
                inventory.pods_error = entry
                components.append(inventory)
                continue
            for pod in entry or []:
                meta = getattr(pod, "metadata", None)
                status = getattr(pod, "status", None)
                name = str(getattr(meta, "name", "") or "").strip()
                if not name:
                    continue
                cpu_fmt, mem_fmt = usage_map.get(name, (None, None))
                inventory.pods.append(
                    PodInventoryItem(
                        name=name,
                        phase=str(getattr(status, "phase", "") or "") or "Unknown",
                        ready=_pod_ready(pod),
                        restarts=_pod_restarts(pod),
                        started_at=_pod_started_at(pod),
                        cpu=cpu_fmt,
                        memory=mem_fmt,
                        cpu_limit=format_cpu_millicores(_container_limit(pod, "cpu")),
                        memory_limit=format_memory_mib(_container_limit(pod, "memory")),
                    )
                )
            components.append(inventory)
        return NamespaceInventory(namespace=ns, components=components)
    finally:
        await api_client.close()


__all__ = (
    "COMPONENT_LABEL_SELECTORS",
    "ComponentInventory",
    "ComponentType",
    "NamespaceInventory",
    "PodInventoryItem",
    "format_cpu_from_nanocores",
    "format_cpu_millicores",
    "format_memory_from_bytes",
    "format_memory_mib",
    "list_component_inventory",
)
