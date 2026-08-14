# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Kubernetes Pod operations backed by ``kubernetes_asyncio``."""

from __future__ import annotations

from inspect import isawaitable
from pathlib import Path
from typing import Any, Literal, Mapping

from ...errors import (
    ErrorCode,
    FrameworkError,
    KubernetesUnavailable,
    NotFoundError,
    PermissionDenied,
)
from .base import PodCreateSpec, PodDeleteResult, PodSummary


class KubernetesAsyncioOperations:
    """Operate managed Pods in one namespace through a shared API client."""

    REQUEST_TIMEOUT_SECONDS = 10
    DELETE_GRACE_PERIOD_SECONDS = 0
    DEFAULT_LABELS = {
        "app.kubernetes.io/managed-by": "openjiuwen-service-demo",
    }

    def __init__(
        self,
        namespace: str,
        *,
        labels: Mapping[str, str] | None = None,
        kubeconfig: str | Path | None = None,
    ) -> None:
        self.namespace = namespace
        self.labels = dict(labels or self.DEFAULT_LABELS)
        self.kubeconfig = str(kubeconfig) if kubeconfig else None
        self._api_client: Any = None
        self._core_api: Any = None
        self._client_module: Any = None

    async def start(self) -> None:
        if self._core_api is not None:
            return
        try:
            from kubernetes_asyncio import client, config
            from kubernetes_asyncio.config.config_exception import ConfigException
        except Exception as exc:
            raise KubernetesUnavailable(
                "kubernetes_asyncio is required for real Kubernetes mode"
            ) from exc

        api_client = None
        try:
            try:
                result = config.load_incluster_config()
                if isawaitable(result):
                    await result
            except ConfigException:
                result = config.load_kube_config(config_file=self.kubeconfig)
                if isawaitable(result):
                    await result
            api_client = client.ApiClient()
            self._client_module = client
            self._api_client = api_client
            self._core_api = client.CoreV1Api(api_client)
        except Exception as exc:
            if api_client is not None:
                result = api_client.close()
                if isawaitable(result):
                    await result
            self._client_module = None
            self._api_client = None
            self._core_api = None
            raise KubernetesUnavailable(
                f"cannot initialize Kubernetes client: {exc}"
            ) from exc

    async def ping(self) -> bool:
        api = self._require_started()
        try:
            await api.list_namespaced_pod(
                namespace=self.namespace,
                limit=1,
                _request_timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
            return True
        except Exception as exc:
            raise self._map_api_exception(exc, operation="list Pods") from exc

    async def close(self) -> None:
        api_client = self._api_client
        self._core_api = None
        self._api_client = None
        self._client_module = None
        if api_client is not None:
            result = api_client.close()
            if isawaitable(result):
                await result

    async def get_pod(self, name: str) -> PodSummary | None:
        api = self._require_started()
        try:
            pod = await api.read_namespaced_pod(
                name=name,
                namespace=self.namespace,
                _request_timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if self._status(exc) == 404:
                return None
            raise self._map_api_exception(exc, operation=f"read Pod {name!r}") from exc
        if not self._is_managed(pod):
            return None
        return self._to_summary(pod)

    async def create_pod(self, spec: PodCreateSpec) -> PodSummary:
        api = self._require_started()
        client = self._client_module
        container = client.V1Container(
            name="demo",
            image=spec.image,
            image_pull_policy="IfNotPresent",
            security_context=client.V1SecurityContext(
                allow_privilege_escalation=False,
                read_only_root_filesystem=False,
                run_as_non_root=True,
                seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                capabilities=client.V1Capabilities(drop=["ALL"]),
            ),
            resources=client.V1ResourceRequirements(
                requests={"cpu": "10m", "memory": "16Mi"},
                limits={"cpu": "100m", "memory": "64Mi"},
            ),
        )
        body = client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=spec.name,
                namespace=self.namespace,
                labels=dict(self.labels),
            ),
            spec=client.V1PodSpec(
                automount_service_account_token=False,
                containers=[container],
                restart_policy="Never",
            ),
        )
        try:
            pod = await api.create_namespaced_pod(
                namespace=self.namespace,
                body=body,
                _request_timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise self._map_api_exception(
                exc,
                operation=f"create Pod {spec.name!r}",
            ) from exc
        return self._to_summary(pod)

    async def delete_pod(self, name: str) -> PodDeleteResult:
        api = self._require_started()
        try:
            pod = await api.read_namespaced_pod(
                name=name,
                namespace=self.namespace,
                _request_timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if self._status(exc) == 404:
                return self._delete_result(name, "already_absent")
            raise self._map_api_exception(exc, operation=f"read Pod {name!r}") from exc

        if not self._is_managed(pod):
            raise NotFoundError(f"pod {name!r} not found")
        metadata = self._value(pod, "metadata")
        if self._value(metadata, "deletion_timestamp") is not None:
            return self._delete_result(name, "deletion_in_progress")
        uid = self._value(metadata, "uid")
        if not uid:
            raise FrameworkError(f"Pod {name!r} response has no UID")

        client = self._client_module
        body = client.V1DeleteOptions(
            grace_period_seconds=self.DELETE_GRACE_PERIOD_SECONDS,
            preconditions=client.V1Preconditions(uid=uid),
        )
        try:
            await api.delete_namespaced_pod(
                name=name,
                namespace=self.namespace,
                body=body,
                grace_period_seconds=self.DELETE_GRACE_PERIOD_SECONDS,
                _request_timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if self._status(exc) == 404:
                return self._delete_result(name, "already_absent")
            raise self._map_api_exception(
                exc,
                operation=f"delete Pod {name!r}",
            ) from exc
        return self._delete_result(name, "delete_requested")

    def _require_started(self) -> Any:
        if self._core_api is None:
            raise KubernetesUnavailable("Kubernetes operations are not started")
        return self._core_api

    def _is_managed(self, pod: Any) -> bool:
        metadata = self._value(pod, "metadata")
        labels = self._value(metadata, "labels") or {}
        return all(labels.get(key) == value for key, value in self.labels.items())

    def _to_summary(self, pod: Any) -> PodSummary:
        metadata = self._value(pod, "metadata")
        spec = self._value(pod, "spec")
        status = self._value(pod, "status")
        name = self._value(metadata, "name")
        namespace = self._value(metadata, "namespace") or self.namespace
        if not name:
            raise FrameworkError("Kubernetes Pod response has no name")

        phase = self._value(status, "phase") or "Unknown"
        if self._value(metadata, "deletion_timestamp") is not None:
            phase = "Terminating"
        conditions = self._value(status, "conditions") or []
        ready_condition = any(
            self._value(condition, "type") == "Ready"
            and str(self._value(condition, "status")).lower() == "true"
            for condition in conditions
        )
        container_statuses = self._value(status, "container_statuses") or []
        containers_ready = bool(container_statuses) and all(
            bool(self._value(item, "ready")) for item in container_statuses
        )
        containers = self._value(spec, "containers") or []
        image = self._value(containers[0], "image") if containers else None
        return PodSummary(
            name=str(name),
            namespace=str(namespace),
            phase=str(phase),
            ready=ready_condition and containers_ready,
            image=str(image) if image is not None else None,
        )

    def _delete_result(
        self,
        name: str,
        state: Literal[
            "delete_requested",
            "deletion_in_progress",
            "already_absent",
        ],
    ) -> PodDeleteResult:
        return PodDeleteResult(
            name=name,
            namespace=self.namespace,
            state=state,
        )

    @classmethod
    def _map_api_exception(cls, exc: Exception, *, operation: str) -> FrameworkError:
        status = cls._status(exc)
        reason = getattr(exc, "reason", None) or str(exc) or exc.__class__.__name__
        message = f"Kubernetes {operation} failed"
        if status is not None:
            message += f" with status {status}"
        message += f": {reason}"
        if status in {401, 403}:
            return PermissionDenied(message)
        if status == 409:
            return FrameworkError(message, code=ErrorCode.CONFLICT)
        if status in {429, 500, 502, 503, 504} or status is None:
            return KubernetesUnavailable(message)
        if status == 404:
            return NotFoundError(message)
        return FrameworkError(message)

    @staticmethod
    def _status(exc: Exception) -> int | None:
        value = getattr(exc, "status", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _value(obj: Any, name: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, Mapping):
            return obj.get(name)
        return getattr(obj, name, None)


__all__ = ["KubernetesAsyncioOperations"]
