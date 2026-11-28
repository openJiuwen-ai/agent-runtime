# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""集群连接状态：HTTP 探活结果 + K8s Pod/资源用量聚合。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance.config_host_probe import runtime_probe_enabled
from manager_server.core.instance.instance_service import get_instance_row
from manager_server.infrastructure.k8s_inventory import (
    ComponentType,
    list_component_inventory,
)
from manager_server.infrastructure.utils import iso_datetime


def _aggregate_agentserver_status(pods: list[dict[str, Any]], *, pods_error: str | None) -> str:
    if pods_error:
        return "unknown"
    if not pods:
        return "pending"
    if all(bool(item.get("ready")) for item in pods):
        return "online"
    if any(str(item.get("phase") or "") == "Failed" for item in pods):
        return "offline"
    if any(bool(item.get("ready")) for item in pods):
        return "pending"
    return "offline"


def _component_status_from_instance(row: Any, ctype: ComponentType) -> tuple[str, str | None]:
    if ctype == "gateway":
        return (
            str(getattr(row, "gateway_status", None) or "pending").strip() or "pending",
            iso_datetime(getattr(row, "gateway_last_alive", None)),
        )
    if ctype == "runtime":
        if not runtime_probe_enabled():
            return "skipped", None
        return (
            str(getattr(row, "runtime_status", None) or "pending").strip() or "pending",
            iso_datetime(getattr(row, "runtime_last_alive", None)),
        )
    return "pending", None


async def build_component_status(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
) -> dict[str, Any] | None:
    row = await get_instance_row(handler, jiuwenclaw_id)
    if row is None:
        return None

    namespace = str(getattr(row, "namespace", None) or "default").strip() or "default"
    inventory = await list_component_inventory(namespace)
    by_type = {item.type: item for item in inventory.components}

    components: list[dict[str, Any]] = []
    for ctype in ("gateway", "runtime", "agentserver"):
        inv = by_type.get(ctype)  # type: ignore[arg-type]
        pods = []
        pods_error = inventory.k8s_error
        metrics_error = inventory.k8s_error
        if inv is not None:
            pods_error = inv.pods_error or pods_error
            metrics_error = inv.metrics_error or metrics_error
            pods = [
                {
                    "name": pod.name,
                    "phase": pod.phase,
                    "ready": pod.ready,
                    "restarts": pod.restarts,
                    "started_at": pod.started_at,
                    "cpu": pod.cpu,
                    "memory": pod.memory,
                    "cpu_limit": pod.cpu_limit,
                    "memory_limit": pod.memory_limit,
                }
                for pod in inv.pods
            ]

        status, last_alive = _component_status_from_instance(row, ctype)  # type: ignore[arg-type]
        if ctype == "agentserver":
            status = _aggregate_agentserver_status(pods, pods_error=pods_error)
            last_alive = None

        components.append(
            {
                "type": ctype,
                "status": status,
                "last_alive": last_alive,
                "pods": pods,
                "pods_error": pods_error,
                "metrics_error": metrics_error,
            }
        )

    return {
        "jiuwenclaw_id": str(getattr(row, "jiuwenclaw_id", "") or ""),
        "namespace": namespace,
        "k8s_error": inventory.k8s_error,
        "components": components,
        "gateway_host": str(getattr(row, "gateway_host", "") or ""),
        "runtime_host": str(getattr(row, "runtime_host", "") or ""),
    }


__all__ = ("build_component_status",)
