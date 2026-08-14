# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Kubernetes Pod capability tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_runtime.service import (
    ErrorCode,
    FakeKubernetesOperations,
    FrameworkError,
    KubernetesAsyncioOperations,
    KubernetesUnavailable,
    NotFoundError,
    PermissionDenied,
    PodCreateSpec,
    PodSummary,
)


@pytest.mark.unit
async def test_fake_kubernetes_lifecycle_and_pod_crud():
    operations = FakeKubernetesOperations("demo")

    assert await operations.ping() is False
    await operations.start()
    assert await operations.ping() is True

    created = await operations.create_pod(
        PodCreateSpec(name="capability-pod-1", image="demo:1")
    )
    assert created == PodSummary(
        name="capability-pod-1",
        namespace="demo",
        phase="Running",
        ready=True,
        image="demo:1",
    )
    assert await operations.get_pod("capability-pod-1") == created

    with pytest.raises(FrameworkError) as conflict:
        await operations.create_pod(
            PodCreateSpec(name="capability-pod-1", image="demo:2")
        )
    assert conflict.value.code == ErrorCode.CONFLICT

    deleted = await operations.delete_pod("capability-pod-1")
    absent = await operations.delete_pod("capability-pod-1")
    assert deleted.state == "delete_requested"
    assert absent.state == "already_absent"
    assert await operations.get_pod("capability-pod-1") is None

    await operations.close()
    assert await operations.ping() is False
    with pytest.raises(KubernetesUnavailable):
        await operations.get_pod("capability-pod-1")


def _pod(
    *,
    labels: dict[str, str] | None = None,
    deletion_timestamp=None,
    uid: str = "uid-1",
):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name="capability-pod-1",
            namespace="demo",
            labels=labels
            or {"app.kubernetes.io/managed-by": "openjiuwen-service-demo"},
            deletion_timestamp=deletion_timestamp,
            uid=uid,
        ),
        spec=SimpleNamespace(
            containers=[SimpleNamespace(image="demo:1")],
        ),
        status=SimpleNamespace(
            phase="Running",
            conditions=[SimpleNamespace(type="Ready", status="True")],
            container_statuses=[SimpleNamespace(ready=True)],
        ),
    )


@pytest.mark.unit
def test_real_adapter_converts_pod_status_and_terminating_phase():
    operations = KubernetesAsyncioOperations("demo")

    running = operations._to_summary(_pod())
    terminating = operations._to_summary(_pod(deletion_timestamp="now"))

    assert running == PodSummary(
        name="capability-pod-1",
        namespace="demo",
        phase="Running",
        ready=True,
        image="demo:1",
    )
    assert terminating.phase == "Terminating"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "error_type", "code"),
    [
        (401, PermissionDenied, ErrorCode.FORBIDDEN),
        (403, PermissionDenied, ErrorCode.FORBIDDEN),
        (404, NotFoundError, ErrorCode.NOT_FOUND),
        (409, FrameworkError, ErrorCode.CONFLICT),
        (429, KubernetesUnavailable, ErrorCode.KUBERNETES_UNAVAILABLE),
        (503, KubernetesUnavailable, ErrorCode.KUBERNETES_UNAVAILABLE),
        (None, KubernetesUnavailable, ErrorCode.KUBERNETES_UNAVAILABLE),
    ],
)
def test_real_adapter_maps_api_errors(status, error_type, code):
    exc = RuntimeError("api unavailable")
    exc.status = status
    exc.reason = "test reason"

    mapped = KubernetesAsyncioOperations._map_api_exception(
        exc,
        operation="test",
    )

    assert isinstance(mapped, error_type)
    assert mapped.code == code
    assert "test reason" in mapped.message


@pytest.mark.unit
async def test_real_adapter_checks_labels_and_delete_state():
    operations = KubernetesAsyncioOperations("demo")
    operations._core_api = SimpleNamespace(
        read_namespaced_pod=AsyncMock(
            side_effect=[
                _pod(labels={"owner": "other"}),
                _pod(labels={"owner": "other"}),
                _pod(deletion_timestamp="now"),
            ]
        )
    )

    assert await operations.get_pod("capability-pod-1") is None
    with pytest.raises(NotFoundError):
        await operations.delete_pod("capability-pod-1")
    result = await operations.delete_pod("capability-pod-1")
    assert result.state == "deletion_in_progress"


@pytest.mark.unit
async def test_real_adapter_delete_uses_uid_precondition():
    client = pytest.importorskip("kubernetes_asyncio.client")
    operations = KubernetesAsyncioOperations("demo")
    operations._client_module = client
    operations._core_api = SimpleNamespace(
        read_namespaced_pod=AsyncMock(return_value=_pod(uid="stable-uid")),
        delete_namespaced_pod=AsyncMock(),
    )

    result = await operations.delete_pod("capability-pod-1")

    assert result.state == "delete_requested"
    call = operations._core_api.delete_namespaced_pod.await_args.kwargs
    assert call["body"].preconditions.uid == "stable-uid"
    assert call["grace_period_seconds"] == 0
    assert call["_request_timeout"] == 10


@pytest.mark.unit
async def test_real_adapter_create_uses_restricted_pod_template():
    client = pytest.importorskip("kubernetes_asyncio.client")
    operations = KubernetesAsyncioOperations("demo")
    operations._client_module = client
    operations._core_api = SimpleNamespace(
        create_namespaced_pod=AsyncMock(return_value=_pod()),
    )

    await operations.create_pod(
        PodCreateSpec(name="capability-pod-1", image="demo:1")
    )

    call = operations._core_api.create_namespaced_pod.await_args.kwargs
    body = call["body"]
    container = body.spec.containers[0]
    security = container.security_context
    assert body.metadata.labels == operations.DEFAULT_LABELS
    assert body.spec.restart_policy == "Never"
    assert body.spec.automount_service_account_token is False
    assert container.name == "demo"
    assert container.image == "demo:1"
    assert container.image_pull_policy == "IfNotPresent"
    assert security.allow_privilege_escalation is False
    assert security.run_as_non_root is True
    assert security.capabilities.drop == ["ALL"]
    assert security.seccomp_profile.type == "RuntimeDefault"
    assert container.resources.requests == {"cpu": "10m", "memory": "16Mi"}
    assert container.resources.limits == {"cpu": "100m", "memory": "64Mi"}


@pytest.mark.unit
async def test_real_adapter_start_falls_back_to_kubeconfig_once():
    pytest.importorskip("kubernetes_asyncio")
    from kubernetes_asyncio.config.config_exception import ConfigException

    api_client = SimpleNamespace(close=AsyncMock())
    core_api = object()
    load_kube_config = AsyncMock()
    with (
        patch(
            "kubernetes_asyncio.config.load_incluster_config",
            side_effect=ConfigException("not in cluster"),
        ) as load_incluster,
        patch(
            "kubernetes_asyncio.config.load_kube_config",
            load_kube_config,
        ),
        patch("kubernetes_asyncio.client.ApiClient", return_value=api_client),
        patch("kubernetes_asyncio.client.CoreV1Api", return_value=core_api),
    ):
        operations = KubernetesAsyncioOperations(
            "demo",
            kubeconfig="C:/kube/config",
        )
        await operations.start()
        await operations.start()

    load_incluster.assert_called_once_with()
    load_kube_config.assert_awaited_once_with(config_file="C:/kube/config")
    assert operations._api_client is api_client
    assert operations._core_api is core_api


@pytest.mark.unit
async def test_real_adapter_returns_absent_for_api_404():
    exc = RuntimeError("not found")
    exc.status = 404
    operations = KubernetesAsyncioOperations("demo")
    operations._core_api = SimpleNamespace(
        read_namespaced_pod=AsyncMock(side_effect=exc),
    )

    assert await operations.get_pod("missing") is None
    result = await operations.delete_pod("missing")
    assert result.state == "already_absent"


@pytest.mark.unit
async def test_real_adapter_ping_reuses_core_api_and_close_is_idempotent():
    api_client = SimpleNamespace(close=AsyncMock())
    operations = KubernetesAsyncioOperations("demo")
    operations._api_client = api_client
    operations._core_api = SimpleNamespace(list_namespaced_pod=AsyncMock())

    assert await operations.ping() is True
    operations._core_api.list_namespaced_pod.assert_awaited_once_with(
        namespace="demo",
        limit=1,
        _request_timeout=10,
    )
    await operations.close()
    await operations.close()
    api_client.close.assert_awaited_once()
