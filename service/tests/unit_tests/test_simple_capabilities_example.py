# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Tests for the deployable simple capabilities demo."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient


_IMPORT_ENV = {
    "DEMO_ENVIRONMENT": "test",
    "DEMO_SERVICE_LABEL": "simple-capabilities-test",
    "DEMO_REDIS_MODE": "fake",
    "DEMO_REDIS_DEFAULT_TTL_SECONDS": "25",
    "DEMO_KUBERNETES_MODE": "fake",
    "DEMO_KUBERNETES_NAMESPACE": "simple-capabilities-demo",
    "DEMO_KUBERNETES_POD_IMAGE": "demo-pod:test",
    "OPENJIUWEN_SERVICE_REDIS_URL": "disabled",
}


@dataclass(frozen=True)
class _InvalidRedisConfigCase:
    mode: str
    ttl: str
    redis_url: str
    message: str


@pytest.fixture(scope="module")
def demo_module():
    with patch.dict(os.environ, _IMPORT_ENV, clear=False):
        yield importlib.import_module("examples.simple_capabilities_app")


@pytest.fixture
def demo_client(demo_module, monkeypatch, tmp_path: Path):
    database_path = tmp_path / "simple-capabilities.db"
    environment = {
        "OPENJIUWEN_SERVICE_DB_TYPE": "sqlite",
        "OPENJIUWEN_SERVICE_DB_NAME": str(database_path),
        "OPENJIUWEN_SERVICE_REDIS_URL": "disabled",
        "OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX": "simple-capabilities-test",
        "OPENJIUWEN_SERVICE_LOCK_BACKEND": "memory",
        "OPENJIUWEN_SERVICE_CACHE_BACKEND": "memory",
        "OPENJIUWEN_SERVICE_DEPLOY_REPLICAS": "1",
        "OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS": "30",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    config = demo_module.DemoConfig(
        environment="test",
        service_label="simple-capabilities-test",
        redis_mode="fake",
        redis_default_ttl_seconds=25,
        kubernetes_mode="fake",
        kubernetes_namespace="simple-capabilities-demo",
        kubernetes_pod_image="demo-pod:test",
    )
    application = demo_module.create_app(config)
    with TestClient(application.asgi) as client:
        yield client


def _envelope(
    msg_type: str,
    rawdata: dict,
    *,
    request_id: str,
    full_metadata: bool = False,
) -> dict:
    metadata = {"request_id": request_id}
    if full_metadata:
        metadata.update(
            {
                "user_id": "demo-user",
                "chat_id": "demo-chat",
                "session_id": "demo-session",
                "bot_id": "demo-bot",
                "channel": "swagger",
                "timestamp": 1786500000,
                "trace_id": "trace-1",
                "instance_id": "demo-instance",
                "extra": {"tenant": "demo"},
            }
        )
    return {
        "type": msg_type,
        "metadata": metadata,
        "rawdata": rawdata,
        "version": "1",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "redis_url"),
    [("fake", "disabled"), ("real", "redis://redis.internal:6379/2")],
)
def test_demo_config_reads_supported_modes(
    demo_module,
    monkeypatch,
    mode: str,
    redis_url: str,
):
    monkeypatch.setenv("DEMO_ENVIRONMENT", "test")
    monkeypatch.setenv("DEMO_SERVICE_LABEL", "demo-test")
    monkeypatch.setenv("DEMO_REDIS_MODE", mode)
    monkeypatch.setenv("DEMO_REDIS_DEFAULT_TTL_SECONDS", "45")
    monkeypatch.setenv("OPENJIUWEN_SERVICE_REDIS_URL", redis_url)
    monkeypatch.setenv("DEMO_KUBERNETES_MODE", mode)
    monkeypatch.setenv("DEMO_KUBERNETES_NAMESPACE", "simple-capabilities-demo")
    monkeypatch.setenv("DEMO_KUBERNETES_POD_IMAGE", "demo-pod:test")

    config = demo_module.DemoConfig.from_env()

    assert config.redis_mode == mode
    assert config.redis_default_ttl_seconds == 45
    assert config.kubernetes_mode == mode


@pytest.mark.unit
@pytest.mark.parametrize(
    "case",
    [
        _InvalidRedisConfigCase("other", "30", "disabled", "DEMO_REDIS_MODE"),
        _InvalidRedisConfigCase("fake", "0", "disabled", "positive integer"),
        _InvalidRedisConfigCase(
            "fake", "invalid", "disabled", "positive integer"
        ),
        _InvalidRedisConfigCase(
            "fake",
            "30",
            "redis://localhost:6379/0",
            "fake Redis mode",
        ),
        _InvalidRedisConfigCase("real", "30", "disabled", "real Redis mode"),
        _InvalidRedisConfigCase("real", "30", "not-a-url", "valid Redis URL"),
    ],
)
def test_demo_config_rejects_invalid_values(
    demo_module,
    monkeypatch,
    case: _InvalidRedisConfigCase,
):
    monkeypatch.setenv("DEMO_ENVIRONMENT", "test")
    monkeypatch.setenv("DEMO_SERVICE_LABEL", "demo-test")
    monkeypatch.setenv("DEMO_REDIS_MODE", case.mode)
    monkeypatch.setenv("DEMO_REDIS_DEFAULT_TTL_SECONDS", case.ttl)
    monkeypatch.setenv("OPENJIUWEN_SERVICE_REDIS_URL", case.redis_url)
    monkeypatch.setenv("DEMO_KUBERNETES_MODE", "fake")
    monkeypatch.setenv("DEMO_KUBERNETES_NAMESPACE", "simple-capabilities-demo")
    monkeypatch.setenv("DEMO_KUBERNETES_POD_IMAGE", "demo-pod:test")

    with pytest.raises(ValueError, match=case.message):
        demo_module.DemoConfig.from_env()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "namespace", "image", "message"),
    [
        ("other", "simple-capabilities-demo", "demo:1", "DEMO_KUBERNETES_MODE"),
        ("fake", "", "demo:1", "DEMO_KUBERNETES_NAMESPACE"),
        ("fake", "UPPERCASE", "demo:1", "DEMO_KUBERNETES_NAMESPACE"),
        ("fake", "simple-capabilities-demo", "", "DEMO_KUBERNETES_POD_IMAGE"),
    ],
)
def test_demo_config_rejects_invalid_kubernetes_values(
    demo_module,
    mode: str,
    namespace: str,
    image: str,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        demo_module.DemoConfig(
            environment="test",
            service_label="demo-test",
            redis_mode="fake",
            redis_default_ttl_seconds=30,
            kubernetes_mode=mode,
            kubernetes_namespace=namespace,
            kubernetes_pod_image=image,
        )


@pytest.mark.unit
def test_database_create_update_read_and_missing(demo_client: TestClient):
    created = demo_client.post(
        "/api/db/write",
        json=_envelope(
            "db/write",
            {"id": "record-1", "value": "first value"},
            request_id="db-write-1",
        ),
    )
    updated = demo_client.post(
        "/api/db/write",
        json=_envelope(
            "db/write",
            {"id": "record-1", "value": "final value"},
            request_id="db-write-2",
        ),
    )
    read = demo_client.post(
        "/api/db/read",
        json=_envelope(
            "db/read",
            {"id": "record-1"},
            request_id="db-read-1",
        ),
    )
    missing = demo_client.post(
        "/api/db/read",
        json=_envelope(
            "db/read",
            {"id": "missing"},
            request_id="db-read-2",
        ),
    )

    assert created.status_code == 200
    assert created.json()["rawdata"]["operation"] == "created"
    assert created.json()["rawdata"]["record"]["updated_at"].endswith("Z")
    assert updated.status_code == 200
    assert updated.json()["rawdata"]["operation"] == "updated"
    assert read.status_code == 200
    assert read.json()["rawdata"]["record"]["value"] == "final value"
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "not_found"


@pytest.mark.unit
def test_redis_write_read_ttl_defaults_and_missing(demo_client: TestClient):
    explicit = demo_client.post(
        "/api/redis/write",
        json=_envelope(
            "redis/write",
            {"key": "explicit", "value": "hello", "ttl_seconds": 120},
            request_id="redis-write-1",
        ),
    )
    defaulted = demo_client.post(
        "/api/redis/write",
        json=_envelope(
            "redis/write",
            {"key": "default", "value": "world"},
            request_id="redis-write-2",
        ),
    )
    read = demo_client.post(
        "/api/redis/read",
        json=_envelope(
            "redis/read",
            {"key": "explicit"},
            request_id="redis-read-1",
        ),
    )
    missing = demo_client.post(
        "/api/redis/read",
        json=_envelope(
            "redis/read",
            {"key": "missing"},
            request_id="redis-read-2",
        ),
    )

    redis = demo_client.app.state.sysctx.redis
    explicit_ttl = demo_client.portal.call(
        redis.ttl, "simple-capabilities-test:kv:explicit"
    )
    default_ttl = demo_client.portal.call(
        redis.ttl, "simple-capabilities-test:kv:default"
    )

    assert explicit.status_code == 200
    assert explicit.json()["rawdata"]["ttl_seconds"] == 120
    assert 0 < explicit_ttl <= 120
    assert defaulted.json()["rawdata"]["ttl_seconds"] == 25
    assert 0 < default_ttl <= 25
    assert read.json()["rawdata"] == {
        "key": "explicit",
        "found": True,
        "value": "hello",
        "backend_mode": "fake",
    }
    assert missing.status_code == 200
    assert missing.json()["rawdata"]["found"] is False
    assert missing.json()["rawdata"]["value"] is None


@pytest.mark.unit
def test_envelope_inspect_returns_envelope_context_and_service(
    demo_client: TestClient,
):
    response = demo_client.post(
        "/api/envelope/inspect",
        json=_envelope(
            "envelope/inspect",
            {
                "message": "inspect this envelope",
                "attributes": {"source": "swagger"},
            },
            request_id="inspect-1",
            full_metadata=True,
        ),
    )

    assert response.status_code == 200
    result = response.json()["rawdata"]
    assert result["envelope"] == {
        "type": "envelope/inspect",
        "version": "1",
        "metadata": {
            "request_id": "inspect-1",
            "user_id": "demo-user",
            "chat_id": "demo-chat",
            "session_id": "demo-session",
            "bot_id": "demo-bot",
            "channel": "swagger",
            "timestamp": 1786500000.0,
            "trace_id": "trace-1",
            "instance_id": "demo-instance",
            "extra": {"tenant": "demo"},
        },
        "rawdata": {
            "message": "inspect this envelope",
            "attributes": {"source": "swagger"},
        },
    }
    assert result["context"] == {
        "msg_type": "envelope/inspect",
        "request_id": "inspect-1",
        "user_id": "demo-user",
        "chat_id": "demo-chat",
        "session_id": "demo-session",
        "bot_id": "demo-bot",
        "channel": "swagger",
        "trace_id": "trace-1",
        "instance_id": "demo-instance",
        "replica_id": result["context"]["replica_id"],
    }
    assert result["context"]["replica_id"]
    assert result["service"] == {
        "environment": "test",
        "service_label": "simple-capabilities-test",
        "redis_mode": "fake",
    }


@pytest.mark.unit
def test_kubernetes_pod_create_read_conflict_delete_and_missing(
    demo_client: TestClient,
):
    created = demo_client.post(
        "/api/k8s/pod/create",
        json=_envelope(
            "k8s/pod/create",
            {"name": "capability-pod-1"},
            request_id="pod-create-1",
        ),
    )
    read = demo_client.post(
        "/api/k8s/pod/read",
        json=_envelope(
            "k8s/pod/read",
            {"name": "capability-pod-1"},
            request_id="pod-read-1",
        ),
    )
    conflict = demo_client.post(
        "/api/k8s/pod/create",
        json=_envelope(
            "k8s/pod/create",
            {"name": "capability-pod-1"},
            request_id="pod-create-2",
        ),
    )
    deleted = demo_client.post(
        "/api/k8s/pod/delete",
        json=_envelope(
            "k8s/pod/delete",
            {"name": "capability-pod-1"},
            request_id="pod-delete-1",
        ),
    )
    absent = demo_client.post(
        "/api/k8s/pod/delete",
        json=_envelope(
            "k8s/pod/delete",
            {"name": "capability-pod-1"},
            request_id="pod-delete-2",
        ),
    )
    missing = demo_client.post(
        "/api/k8s/pod/read",
        json=_envelope(
            "k8s/pod/read",
            {"name": "capability-pod-1"},
            request_id="pod-read-2",
        ),
    )

    assert created.status_code == 200
    assert created.json()["rawdata"] == {
        "operation": "created",
        "pod": {
            "name": "capability-pod-1",
            "namespace": "simple-capabilities-demo",
            "phase": "Running",
            "ready": True,
            "image": "demo-pod:test",
        },
    }
    assert read.status_code == 200
    assert read.json()["rawdata"]["pod"] == created.json()["rawdata"]["pod"]
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "conflict"
    assert deleted.json()["rawdata"]["state"] == "delete_requested"
    assert absent.json()["rawdata"]["state"] == "already_absent"
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "not_found"


@pytest.mark.unit
def test_kubernetes_pod_name_validation_and_envelope_type(demo_client: TestClient):
    invalid = demo_client.post(
        "/api/k8s/pod/create",
        json=_envelope(
            "k8s/pod/create",
            {"name": "Invalid_Pod"},
            request_id="pod-invalid-1",
        ),
    )
    wrong_type = demo_client.post(
        "/api/k8s/pod/create",
        json=_envelope(
            "k8s/pod/read",
            {"name": "capability-pod-2"},
            request_id="pod-invalid-2",
        ),
    )

    assert invalid.status_code == 400
    assert invalid.json()["error_code"] == "validation"
    assert wrong_type.status_code == 422

    long_label = demo_client.post(
        "/api/k8s/pod/create",
        json=_envelope(
            "k8s/pod/create",
            {"name": f"{'a' * 64}.valid"},
            request_id="pod-invalid-3",
        ),
    )
    assert long_label.status_code == 400
    assert long_label.json()["error_code"] == "validation"


@pytest.mark.unit
def test_docs_openapi_and_validation_error(demo_client: TestClient):
    docs = demo_client.get("/docs")
    openapi_response = demo_client.get("/openapi.json")
    schema = openapi_response.json()
    expected = {
        "/api/db/write": "db/write",
        "/api/db/read": "db/read",
        "/api/redis/write": "redis/write",
        "/api/redis/read": "redis/read",
        "/api/envelope/inspect": "envelope/inspect",
        "/api/k8s/pod/read": "k8s/pod/read",
        "/api/k8s/pod/create": "k8s/pod/create",
        "/api/k8s/pod/delete": "k8s/pod/delete",
    }

    assert docs.status_code == 200
    assert openapi_response.status_code == 200
    for path, msg_type in expected.items():
        operation = schema["paths"][path]
        assert set(operation) == {"post"}
        request_ref = operation["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[1]]
        assert request_schema["required"] == ["metadata", "rawdata"]
        assert request_schema["properties"]["type"]["const"] == msg_type
        assert "application/json" in operation["post"]["responses"]["200"]["content"]

    invalid = demo_client.post(
        "/api/db/write",
        json=_envelope(
            "db/write",
            {"id": "record-without-value"},
            request_id="invalid-1",
        ),
    )
    wrong_type = demo_client.post(
        "/api/db/write",
        json=_envelope(
            "db/read",
            {"id": "record-1"},
            request_id="invalid-2",
        ),
    )

    assert invalid.status_code == 400
    assert invalid.json()["error_code"] == "validation"
    assert wrong_type.status_code == 422
