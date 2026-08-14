# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Database, Redis, Envelope, and Kubernetes capability application."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    TableDefinition,
)
from openjiuwen_runtime.service import (
    App,
    DatabaseUnavailable,
    Envelope,
    FakeKubernetesOperations,
    KubernetesAsyncioOperations,
    NotFoundError,
    PodCreateSpec,
    RedisUnavailable,
    ServiceConfig,
    SystemContext,
    TypedAppContext,
    build_system_context,
)


RUNTIME_CAPABILITIES_TABLE_NAME = "runtime_capability_records"
RUNTIME_CAPABILITIES_TABLE = TableDefinition(
    table_name=RUNTIME_CAPABILITIES_TABLE_NAME,
    columns=[
        ColumnDefinition("id", "string", primary_key=True, nullable=False, length=64),
        ColumnDefinition("value", "string", nullable=False, length=4096),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

_DISABLED_REDIS_URLS = {"disabled", "none"}
_DNS_1123_LABEL_PATTERN = r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$"
_DNS_1123_SUBDOMAIN_PATTERN = (
    r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)*$"
)


@dataclass(frozen=True)
class RuntimeCapabilitiesConfig:
    """Application settings and the local/server resource policy."""

    mode: Literal["local", "server"]
    service_label: str
    redis_default_ttl_seconds: int
    kubernetes_namespace: str
    kubernetes_pod_image: str
    kubernetes_pod_run_as_user: int | None = None
    kubernetes_pod_run_as_group: int | None = None
    sqlite_path: str = "./runtime_capabilities.db"

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().lower()
        service_label = str(self.service_label).strip()
        kubernetes_namespace = str(self.kubernetes_namespace).strip()
        kubernetes_pod_image = str(self.kubernetes_pod_image).strip()
        sqlite_path = str(self.sqlite_path).strip()
        if mode not in {"local", "server"}:
            raise ValueError("RUNTIME_CAPABILITIES_MODE must be local or server")
        if not service_label:
            raise ValueError("RUNTIME_CAPABILITIES_SERVICE_LABEL must not be empty")
        if not re.fullmatch(_DNS_1123_LABEL_PATTERN, kubernetes_namespace):
            raise ValueError(
                "RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE must be a valid "
                "DNS-1123 label"
            )
        if not kubernetes_pod_image:
            raise ValueError(
                "RUNTIME_CAPABILITIES_KUBERNETES_POD_IMAGE must not be empty"
            )
        if not sqlite_path:
            raise ValueError("RUNTIME_CAPABILITIES_SQLITE_PATH must not be empty")
        ttl = self.redis_default_ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ValueError(
                "RUNTIME_CAPABILITIES_REDIS_DEFAULT_TTL_SECONDS must be a "
                "positive integer"
            )
        run_as_user = self.kubernetes_pod_run_as_user
        run_as_group = self.kubernetes_pod_run_as_group
        if (run_as_user is None) != (run_as_group is None):
            raise ValueError(
                "RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER and "
                "RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_GROUP must be "
                "configured together"
            )
        for name, value in (
            ("RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER", run_as_user),
            ("RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_GROUP", run_as_group),
        ):
            if value is not None and (isinstance(value, bool) or value <= 0):
                raise ValueError(f"{name} must be a positive integer")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "service_label", service_label)
        object.__setattr__(self, "kubernetes_namespace", kubernetes_namespace)
        object.__setattr__(self, "kubernetes_pod_image", kubernetes_pod_image)
        object.__setattr__(self, "sqlite_path", sqlite_path)

    @property
    def backend_mode(self) -> Literal["fake", "real"]:
        return "fake" if self.mode == "local" else "real"

    @classmethod
    def from_env(cls) -> "RuntimeCapabilitiesConfig":
        ttl = _positive_int(
            "RUNTIME_CAPABILITIES_REDIS_DEFAULT_TTL_SECONDS",
            "300",
        )
        run_as_user = _optional_positive_int(
            "RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_USER"
        )
        run_as_group = _optional_positive_int(
            "RUNTIME_CAPABILITIES_KUBERNETES_POD_RUN_AS_GROUP"
        )
        return cls(
            mode=os.getenv("RUNTIME_CAPABILITIES_MODE", "local"),
            service_label=os.getenv(
                "RUNTIME_CAPABILITIES_SERVICE_LABEL", "runtime-capabilities"
            ),
            redis_default_ttl_seconds=ttl,
            kubernetes_namespace=os.getenv(
                "RUNTIME_CAPABILITIES_KUBERNETES_NAMESPACE",
                "runtime-capabilities",
            ),
            kubernetes_pod_image=os.getenv(
                "RUNTIME_CAPABILITIES_KUBERNETES_POD_IMAGE",
                "runtime-capabilities-pod:local",
            ),
            kubernetes_pod_run_as_user=run_as_user,
            kubernetes_pod_run_as_group=run_as_group,
            sqlite_path=os.getenv(
                "RUNTIME_CAPABILITIES_SQLITE_PATH",
                "./runtime_capabilities.db",
            ),
        )


def _positive_int(name: str, default: str) -> int:
    value = os.getenv(name, default)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _optional_positive_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def build_service_config(config: RuntimeCapabilitiesConfig) -> ServiceConfig:
    """Build framework settings from the selected application mode."""
    if config.mode == "local":
        return ServiceConfig(
            host=os.getenv("OPENJIUWEN_SERVICE_HOST", "127.0.0.1"),
            port=int(os.getenv("OPENJIUWEN_SERVICE_PORT", "8090")),
            redis_url="disabled",
            key_prefix=os.getenv(
                "OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX", "runtime-capabilities"
            ),
            title="Runtime Capabilities",
            request_timeout_seconds=float(
                os.getenv("OPENJIUWEN_SERVICE_REQUEST_TIMEOUT_SECONDS", "30")
            ),
            lock_backend="memory",
            cache_backend="memory",
            deploy_replicas=1,
            db_type="sqlite",
            db_name=config.sqlite_path,
        )

    service_config = ServiceConfig.from_env()
    if service_config.db_type != "mysql":
        raise ValueError("server mode requires OPENJIUWEN_SERVICE_DB_TYPE=mysql")
    redis_url = (service_config.redis_url or "").strip()
    if not redis_url or redis_url.lower() in _DISABLED_REDIS_URLS:
        raise ValueError("server mode requires OPENJIUWEN_SERVICE_REDIS_URL")
    parsed = urlparse(redis_url)
    network_url = parsed.scheme in {"redis", "rediss"} and parsed.hostname
    unix_url = parsed.scheme == "unix" and parsed.path
    if not (network_url or unix_url):
        raise ValueError("OPENJIUWEN_SERVICE_REDIS_URL must be a valid Redis URL")
    return service_config


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DbWriteInput(_StrictInput):
    id: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=4096)


class DbReadInput(_StrictInput):
    id: str = Field(min_length=1, max_length=64)


class RedisWriteInput(_StrictInput):
    key: str = Field(min_length=1, max_length=256)
    value: str = Field(min_length=1, max_length=4096)
    ttl_seconds: int | None = Field(default=None, gt=0)


class RedisReadInput(_StrictInput):
    key: str = Field(min_length=1, max_length=256)


class EnvelopeInspectInput(_StrictInput):
    message: str = Field(min_length=1, max_length=4096)
    attributes: dict[str, Any] = Field(default_factory=dict)


class PodNameInput(_StrictInput):
    name: str = Field(
        min_length=1,
        max_length=253,
        pattern=_DNS_1123_SUBDOMAIN_PATTERN,
    )


class RecordView(BaseModel):
    id: str
    value: str
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_updated_at(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DbWriteOutput(BaseModel):
    operation: Literal["created", "updated"]
    record: RecordView


class DbReadOutput(BaseModel):
    record: RecordView


class RedisWriteOutput(BaseModel):
    key: str
    value: str
    ttl_seconds: int
    backend_mode: Literal["fake", "real"]


class RedisReadOutput(BaseModel):
    key: str
    found: bool
    value: str | None
    backend_mode: Literal["fake", "real"]


class MetadataView(BaseModel):
    request_id: str
    user_id: str | None
    chat_id: str | None
    session_id: str | None
    bot_id: str | None
    channel: str | None
    timestamp: float | None
    trace_id: str | None
    instance_id: str | None
    extra: dict[str, Any]


class InspectedEnvelopeView(BaseModel):
    type: str
    version: str
    metadata: MetadataView
    rawdata: EnvelopeInspectInput


class RequestContextView(BaseModel):
    msg_type: str
    request_id: str
    user_id: str | None
    chat_id: str | None
    session_id: str | None
    bot_id: str | None
    channel: str | None
    trace_id: str | None
    instance_id: str | None
    replica_id: str


class ServiceView(BaseModel):
    mode: Literal["local", "server"]
    service_label: str
    redis_mode: Literal["fake", "real"]
    kubernetes_mode: Literal["fake", "real"]


class EnvelopeInspectOutput(BaseModel):
    envelope: InspectedEnvelopeView
    context: RequestContextView
    service: ServiceView


class PodView(BaseModel):
    name: str
    namespace: str
    phase: str
    ready: bool
    image: str | None


class PodReadOutput(BaseModel):
    pod: PodView


class PodCreateOutput(BaseModel):
    operation: Literal["created"]
    pod: PodView


class PodDeleteOutput(BaseModel):
    name: str
    namespace: str
    state: Literal[
        "delete_requested",
        "deletion_in_progress",
        "already_absent",
    ]


def build_runtime_capabilities_system_context(
    service_config: ServiceConfig,
    config: RuntimeCapabilitiesConfig,
) -> SystemContext:
    """Build resources for the selected local or server deployment mode."""
    redis_client = None
    labels = {
        "app.kubernetes.io/managed-by": "openjiuwen-runtime-capabilities",
    }
    if config.mode == "local":
        import fakeredis.aioredis

        redis_client = fakeredis.aioredis.FakeRedis()
        kubernetes = FakeKubernetesOperations(
            config.kubernetes_namespace,
            labels=labels,
        )
    else:
        kubernetes = KubernetesAsyncioOperations(
            config.kubernetes_namespace,
            labels=labels,
            kubeconfig=os.getenv("KUBECONFIG"),
        )

    system = build_system_context(
        service_config,
        redis=redis_client,
        kubernetes=kubernetes,
        table_definitions=(RUNTIME_CAPABILITIES_TABLE,),
    )
    if redis_client is not None:
        system.set_redis(redis_client, owned=True)
    system.set_kubernetes(kubernetes, owned=True)
    return system


def _record_view(record: Any) -> RecordView:
    if isinstance(record, Mapping):
        values = record
    else:
        to_dict = getattr(record, "to_dict", None)
        if callable(to_dict):
            values = to_dict()
        else:
            values = {
                "id": record.id,
                "value": record.value,
                "updated_at": record.updated_at,
            }
    return RecordView.model_validate(values)


def register_handlers(application: App, config: RuntimeCapabilitiesConfig) -> None:
    @application.handle(
        "db/write",
        request_model=DbWriteInput,
        response_model=DbWriteOutput,
        summary="Write a database record",
        description="Create or update one record by ID.",
        tags=["Database"],
    )
    async def write_database(
        ctx: TypedAppContext[DbWriteInput],
        env: Envelope[DbWriteInput],
    ) -> dict[str, Any]:
        request = ctx.request
        updated_at = datetime.now(timezone.utc)
        try:
            existing = await ctx.db_get(
                RUNTIME_CAPABILITIES_TABLE_NAME, {"id": request.id}
            )
            if existing is None:
                operation = "created"
                await ctx.db_create(
                    RUNTIME_CAPABILITIES_TABLE_NAME,
                    {
                        "id": request.id,
                        "value": request.value,
                        "updated_at": updated_at,
                    },
                )
            else:
                operation = "updated"
                await ctx.db_update(
                    RUNTIME_CAPABILITIES_TABLE_NAME,
                    {"id": request.id},
                    {"value": request.value, "updated_at": updated_at},
                )
            record = await ctx.db_get(
                RUNTIME_CAPABILITIES_TABLE_NAME, {"id": request.id}
            )
        except DatabaseUnavailable:
            raise
        except Exception as exc:
            raise DatabaseUnavailable("database operation failed") from exc
        if record is None:
            raise DatabaseUnavailable("database write did not return a record")
        return {
            "operation": operation,
            "record": _record_view(record).model_dump(mode="python"),
        }

    @application.handle(
        "db/read",
        request_model=DbReadInput,
        response_model=DbReadOutput,
        summary="Read a database record",
        description="Read one record by ID.",
        tags=["Database"],
    )
    async def read_database(
        ctx: TypedAppContext[DbReadInput],
        env: Envelope[DbReadInput],
    ) -> dict[str, Any]:
        try:
            record = await ctx.db_get(
                RUNTIME_CAPABILITIES_TABLE_NAME, {"id": ctx.request.id}
            )
        except DatabaseUnavailable:
            raise
        except Exception as exc:
            raise DatabaseUnavailable("database operation failed") from exc
        if record is None:
            raise NotFoundError(f"record {ctx.request.id!r} not found")
        return {"record": _record_view(record).model_dump(mode="python")}

    @application.handle(
        "redis/write",
        request_model=RedisWriteInput,
        response_model=RedisWriteOutput,
        summary="Write a Redis value",
        description="Write one namespaced Redis key with a TTL.",
        tags=["Redis"],
    )
    async def write_redis(
        ctx: TypedAppContext[RedisWriteInput],
        env: Envelope[RedisWriteInput],
    ) -> dict[str, Any]:
        request = ctx.request
        ttl = request.ttl_seconds or config.redis_default_ttl_seconds
        try:
            await ctx.kv.set(request.key, request.value, ttl=ttl)
        except RedisUnavailable:
            raise
        except Exception as exc:
            raise RedisUnavailable("Redis write failed") from exc
        return {
            "key": request.key,
            "value": request.value,
            "ttl_seconds": ttl,
            "backend_mode": config.backend_mode,
        }

    @application.handle(
        "redis/read",
        request_model=RedisReadInput,
        response_model=RedisReadOutput,
        summary="Read a Redis value",
        description="Read one namespaced Redis key.",
        tags=["Redis"],
    )
    async def read_redis(
        ctx: TypedAppContext[RedisReadInput],
        env: Envelope[RedisReadInput],
    ) -> dict[str, Any]:
        try:
            value = await ctx.kv.get(ctx.request.key)
        except RedisUnavailable:
            raise
        except Exception as exc:
            raise RedisUnavailable("Redis read failed") from exc
        return {
            "key": ctx.request.key,
            "found": value is not None,
            "value": value,
            "backend_mode": config.backend_mode,
        }

    @application.handle(
        "envelope/inspect",
        request_model=EnvelopeInspectInput,
        response_model=EnvelopeInspectOutput,
        summary="Inspect an Envelope",
        description="Return parsed Envelope, Metadata, and request context fields.",
        tags=["Envelope"],
    )
    async def inspect_envelope(
        ctx: TypedAppContext[EnvelopeInspectInput],
        env: Envelope[EnvelopeInspectInput],
    ) -> dict[str, Any]:
        return {
            "envelope": env.to_dict(),
            "context": {
                "msg_type": ctx.msg_type,
                "request_id": ctx.request_id,
                "user_id": ctx.user_id,
                "chat_id": ctx.chat_id,
                "session_id": ctx.session_id,
                "bot_id": ctx.bot_id,
                "channel": ctx.channel,
                "trace_id": ctx.trace_id,
                "instance_id": ctx.instance_id,
                "replica_id": ctx.replica_id,
            },
            "service": {
                "mode": config.mode,
                "service_label": config.service_label,
                "redis_mode": config.backend_mode,
                "kubernetes_mode": config.backend_mode,
            },
        }

    @application.handle(
        "k8s/pod/read",
        request_model=PodNameInput,
        response_model=PodReadOutput,
        summary="Read a managed Kubernetes Pod",
        description="Read lifecycle fields for one application-managed Pod.",
        tags=["Kubernetes"],
    )
    async def read_pod(
        ctx: TypedAppContext[PodNameInput],
        env: Envelope[PodNameInput],
    ) -> dict[str, Any]:
        pod = await ctx.kubernetes.get_pod(ctx.request.name)
        if pod is None:
            raise NotFoundError(f"pod {ctx.request.name!r} not found")
        return {"pod": asdict(pod)}

    @application.handle(
        "k8s/pod/create",
        request_model=PodNameInput,
        response_model=PodCreateOutput,
        summary="Create a managed Kubernetes Pod",
        description="Create one Pod from the configured restricted template.",
        tags=["Kubernetes"],
    )
    async def create_pod(
        ctx: TypedAppContext[PodNameInput],
        env: Envelope[PodNameInput],
    ) -> dict[str, Any]:
        pod = await ctx.kubernetes.create_pod(
            PodCreateSpec(
                name=ctx.request.name,
                image=config.kubernetes_pod_image,
                run_as_user=config.kubernetes_pod_run_as_user,
                run_as_group=config.kubernetes_pod_run_as_group,
            )
        )
        return {"operation": "created", "pod": asdict(pod)}

    @application.handle(
        "k8s/pod/delete",
        request_model=PodNameInput,
        response_model=PodDeleteOutput,
        summary="Delete a managed Kubernetes Pod",
        description="Submit deletion for one application-managed Pod.",
        tags=["Kubernetes"],
    )
    async def delete_pod(
        ctx: TypedAppContext[PodNameInput],
        env: Envelope[PodNameInput],
    ) -> dict[str, Any]:
        return asdict(await ctx.kubernetes.delete_pod(ctx.request.name))


def create_app(config: RuntimeCapabilitiesConfig | None = None) -> App:
    resolved_config = config or RuntimeCapabilitiesConfig.from_env()

    def create_system_context() -> SystemContext:
        service_config = build_service_config(resolved_config)
        return build_runtime_capabilities_system_context(
            service_config,
            resolved_config,
        )

    application = App(
        create_system_context,
        enable_ws=False,
        title="Runtime Capabilities",
    )

    @application.asgi.get(
        "/health",
        summary="Check application health",
        tags=["Operations"],
    )
    async def health() -> dict[str, str]:
        return {
            "status": "healthy",
            "application": "runtime-capabilities",
            "mode": resolved_config.mode,
        }

    register_handlers(application, resolved_config)
    return application


app = create_app()
asgi = app.asgi


if __name__ == "__main__":
    app.run()
