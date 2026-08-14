# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Unit and HTTP system tests for the runtime capabilities application."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from runtime_capabilities.application import (
    RuntimeCapabilitiesConfig,
    build_service_config,
    create_app,
)


@pytest.fixture
def local_config(tmp_path: Path) -> RuntimeCapabilitiesConfig:
    return RuntimeCapabilitiesConfig(
        mode="local",
        service_label="runtime-capabilities-test",
        redis_default_ttl_seconds=25,
        kubernetes_namespace="runtime-capabilities-test",
        kubernetes_pod_image="runtime-capabilities-pod:test",
        sqlite_path=str(tmp_path / "runtime-capabilities.db"),
    )


@pytest.fixture
def application_client(local_config: RuntimeCapabilitiesConfig):
    environment = {
        "OPENJIUWEN_SERVICE_HOST": "127.0.0.1",
        "OPENJIUWEN_SERVICE_PORT": "8090",
        "OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX": "runtime-capabilities-test",
        "OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS": "30",
    }
    with patch.dict(os.environ, environment, clear=False):
        application = create_app(local_config)
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
                "user_id": "example-user",
                "chat_id": "example-chat",
                "session_id": "example-session",
                "bot_id": "example-bot",
                "channel": "swagger",
                "timestamp": 1786500000,
                "trace_id": "trace-1",
                "instance_id": "example-instance",
                "extra": {"tenant": "example-tenant"},
            }
        )
    return {
        "type": msg_type,
        "metadata": metadata,
        "rawdata": rawdata,
        "version": "1",
    }


@pytest.mark.unit
def test_config_reads_local_mode_and_optional_security_context():
    environment = {
        "RUNTIME_CAPABILITIES_MODE": "local",
        "RUNTIME_CAPABILITIES_SERVICE_LABEL": "runtime-capabilities-test",
        "RUNTIME_CAPABILITIES_REDIS_DEFAULT_TTL_SECONDS": "45",
        "RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE": "runtime-capabilities-test",
        "RUNTIME_CAPABILITIES_KUBERNETES_POD_IMAGE": "example:test",
        "RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER": "999",
        "RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_GROUP": "1000",
        "RUNTIME_CAPABILITIES_SQLITE_PATH": "./example.db",
    }
    with patch.dict(os.environ, environment, clear=True):
        config = RuntimeCapabilitiesConfig.from_env()

    assert config.mode == "local"
    assert config.backend_mode == "fake"
    assert config.redis_default_ttl_seconds == 45
    assert config.kubernetes_pod_run_as_user == 999
    assert config.kubernetes_pod_run_as_group == 1000
    assert config.sqlite_path == "./example.db"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"RUNTIME_CAPABILITIES_MODE": "invalid"}, "MODE"),
        (
            {"RUNTIME_CAPABILITIES_REDIS_DEFAULT_TTL_SECONDS": "0"},
            "REDIS_DEFAULT_TTL_SECONDS",
        ),
        (
            {"RUNTIME_CAPABILITIES_REDIS_DEFAULT_TTL_SECONDS": "invalid"},
            "REDIS_DEFAULT_TTL_SECONDS",
        ),
        (
            {"RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE": "Invalid_Name"},
            "KUBERNETES_NAMESPACE",
        ),
        (
            {"RUNTIME_CAPABILITIES_KUBERNETES_POD_IMAGE": ""},
            "KUBERNETES_POD_IMAGE",
        ),
        (
            {"RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER": "999"},
            "configured together",
        ),
        (
            {"RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER": "invalid"},
            "POD_RUN_AS_USER",
        ),
    ],
)
def test_config_rejects_invalid_values(overrides: dict[str, str], message: str):
    environment = {
        "RUNTIME_CAPABILITIES_MODE": "local",
        "RUNTIME_CAPABILITIES_SERVICE_LABEL": "runtime-capabilities-test",
        "RUNTIME_CAPABILITIES_REDIS_DEFAULT_TTL_SECONDS": "30",
        "RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE": "runtime-capabilities-test",
        "RUNTIME_CAPABILITIES_KUBERNETES_POD_IMAGE": "example:test",
        "RUNTIME_CAPABILITIES_SQLITE_PATH": "./example.db",
        **overrides,
    }
    with (
        patch.dict(os.environ, environment, clear=True),
        pytest.raises(ValueError, match=message),
    ):
        RuntimeCapabilitiesConfig.from_env()


@pytest.mark.unit
def test_local_mode_builds_sqlite_memory_service_config(
    local_config: RuntimeCapabilitiesConfig,
):
    with patch.dict(os.environ, {}, clear=True):
        service_config = build_service_config(local_config)

    assert service_config.host == "127.0.0.1"
    assert service_config.port == 8090
    assert service_config.db_type == "sqlite"
    assert service_config.db_name == local_config.sqlite_path
    assert service_config.redis_url == "disabled"
    assert service_config.lock_backend == "memory"
    assert service_config.cache_backend == "memory"


@pytest.mark.unit
def test_server_mode_requires_mysql_and_redis():
    config = RuntimeCapabilitiesConfig(
        mode="server",
        service_label="runtime-capabilities-test",
        redis_default_ttl_seconds=30,
        kubernetes_namespace="runtime-capabilities-test",
        kubernetes_pod_image="example:test",
    )
    base_environment = {
        "OPENJIUWEN_SERVICE_DB_TYPE": "mysql",
        "OPENJIUWEN_SERVICE_DB_HOST": "mysql.internal",
        "OPENJIUWEN_SERVICE_DB_PORT": "3306",
        "OPENJIUWEN_SERVICE_DB_NAME": "runtime_capabilities",
        "OPENJIUWEN_SERVICE_DB_USER": "runtime_capabilities",
        "OPENJIUWEN_SERVICE_DB_PASSWORD": "test-password",
        "OPENJIUWEN_SERVICE_REDIS_URL": "redis://redis.internal:6379/0",
        "OPENJIUWEN_SERVICE_LOCK_BACKEND": "redis",
    }
    with patch.dict(os.environ, base_environment, clear=True):
        service_config = build_service_config(config)
    assert service_config.db_type == "mysql"
    assert service_config.redis_url == "redis://redis.internal:6379/0"

    with patch.dict(
        os.environ,
        {**base_environment, "OPENJIUWEN_SERVICE_DB_TYPE": "sqlite"},
        clear=True,
    ):
        with pytest.raises(ValueError, match="DB_TYPE=mysql"):
            build_service_config(config)

    with patch.dict(
        os.environ,
        {**base_environment, "OPENJIUWEN_SERVICE_REDIS_URL": "disabled"},
        clear=True,
    ):
        with pytest.raises(ValueError, match="REDIS_URL"):
            build_service_config(config)


@pytest.mark.system
def test_database_create_update_read_and_missing(application_client: TestClient):
    created = application_client.post(
        "/api/db/write",
        json=_envelope(
            "db/write",
            {"id": "record-1", "value": "first value"},
            request_id="db-write-1",
        ),
    )
    updated = application_client.post(
        "/api/db/write",
        json=_envelope(
            "db/write",
            {"id": "record-1", "value": "final value"},
            request_id="db-write-2",
        ),
    )
    read = application_client.post(
        "/api/db/read",
        json=_envelope(
            "db/read",
            {"id": "record-1"},
            request_id="db-read-1",
        ),
    )
    missing = application_client.post(
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


@pytest.mark.system
def test_redis_write_read_ttl_defaults_and_missing(application_client: TestClient):
    explicit = application_client.post(
        "/api/redis/write",
        json=_envelope(
            "redis/write",
            {"key": "explicit", "value": "hello", "ttl_seconds": 120},
            request_id="redis-write-1",
        ),
    )
    defaulted = application_client.post(
        "/api/redis/write",
        json=_envelope(
            "redis/write",
            {"key": "default", "value": "world"},
            request_id="redis-write-2",
        ),
    )
    read = application_client.post(
        "/api/redis/read",
        json=_envelope(
            "redis/read",
            {"key": "explicit"},
            request_id="redis-read-1",
        ),
    )
    missing = application_client.post(
        "/api/redis/read",
        json=_envelope(
            "redis/read",
            {"key": "missing"},
            request_id="redis-read-2",
        ),
    )

    redis = application_client.app.state.sysctx.redis
    explicit_ttl = application_client.portal.call(
        redis.ttl,
        "runtime-capabilities-test:kv:explicit",
    )
    default_ttl = application_client.portal.call(
        redis.ttl,
        "runtime-capabilities-test:kv:default",
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


@pytest.mark.system
def test_envelope_inspect_returns_envelope_context_and_service(
    application_client: TestClient,
):
    response = application_client.post(
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
    assert result["envelope"]["metadata"]["request_id"] == "inspect-1"
    assert result["envelope"]["metadata"]["extra"] == {"tenant": "example-tenant"}
    assert result["context"]["msg_type"] == "envelope/inspect"
    assert result["context"]["user_id"] == "example-user"
    assert result["context"]["replica_id"]
    assert result["service"] == {
        "mode": "local",
        "service_label": "runtime-capabilities-test",
        "redis_mode": "fake",
        "kubernetes_mode": "fake",
    }


@pytest.mark.system
def test_kubernetes_pod_create_read_conflict_delete_and_missing(
    application_client: TestClient,
):
    created = application_client.post(
        "/api/k8s/pod/create",
        json=_envelope(
            "k8s/pod/create",
            {"name": "capability-pod-1"},
            request_id="pod-create-1",
        ),
    )
    read = application_client.post(
        "/api/k8s/pod/read",
        json=_envelope(
            "k8s/pod/read",
            {"name": "capability-pod-1"},
            request_id="pod-read-1",
        ),
    )
    conflict = application_client.post(
        "/api/k8s/pod/create",
        json=_envelope(
            "k8s/pod/create",
            {"name": "capability-pod-1"},
            request_id="pod-create-2",
        ),
    )
    deleted = application_client.post(
        "/api/k8s/pod/delete",
        json=_envelope(
            "k8s/pod/delete",
            {"name": "capability-pod-1"},
            request_id="pod-delete-1",
        ),
    )
    absent = application_client.post(
        "/api/k8s/pod/delete",
        json=_envelope(
            "k8s/pod/delete",
            {"name": "capability-pod-1"},
            request_id="pod-delete-2",
        ),
    )
    missing = application_client.post(
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
            "namespace": "runtime-capabilities-test",
            "phase": "Running",
            "ready": True,
            "image": "runtime-capabilities-pod:test",
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


@pytest.mark.system
def test_docs_health_validation_and_path_type_binding(
    application_client: TestClient,
):
    health = application_client.get("/health")
    docs = application_client.get("/docs")
    openapi_response = application_client.get("/openapi.json")
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

    assert health.status_code == 200
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

    invalid = application_client.post(
        "/api/db/write",
        json=_envelope(
            "db/write",
            {"id": "record-without-value"},
            request_id="invalid-1",
        ),
    )
    wrong_type = application_client.post(
        "/api/db/write",
        json=_envelope(
            "db/read",
            {"id": "record-1"},
            request_id="invalid-2",
        ),
    )
    unknown = application_client.post(
        "/api/unknown",
        json=_envelope(
            "unknown",
            {},
            request_id="unknown-1",
        ),
    )

    assert invalid.status_code == 400
    assert invalid.json()["error_code"] == "validation"
    assert wrong_type.status_code == 422
    assert unknown.status_code == 404
