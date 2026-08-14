# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Deployable demo for database, Redis, Envelope, and Kubernetes capabilities."""

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


DEMO_TABLE_NAME = "simple_capability_records"
DEMO_TABLE = TableDefinition(
    table_name=DEMO_TABLE_NAME,
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
class DemoConfig:
    """Configuration owned by the simple capabilities demo."""

    environment: str
    service_label: str
    redis_mode: Literal["fake", "real"]
    redis_default_ttl_seconds: int
    kubernetes_mode: Literal["fake", "real"]
    kubernetes_namespace: str
    kubernetes_pod_image: str

    def __post_init__(self) -> None:
        environment = str(self.environment).strip()
        service_label = str(self.service_label).strip()
        redis_mode = str(self.redis_mode).strip().lower()
        kubernetes_mode = str(self.kubernetes_mode).strip().lower()
        kubernetes_namespace = str(self.kubernetes_namespace).strip()
        kubernetes_pod_image = str(self.kubernetes_pod_image).strip()
        if not environment:
            raise ValueError("DEMO_ENVIRONMENT must not be empty")
        if not service_label:
            raise ValueError("DEMO_SERVICE_LABEL must not be empty")
        if redis_mode not in {"fake", "real"}:
            raise ValueError("DEMO_REDIS_MODE must be one of fake, real")
        if kubernetes_mode not in {"fake", "real"}:
            raise ValueError("DEMO_KUBERNETES_MODE must be one of fake, real")
        if not re.fullmatch(_DNS_1123_LABEL_PATTERN, kubernetes_namespace):
            raise ValueError(
                "DEMO_KUBERNETES_NAMESPACE must be a valid DNS-1123 label"
            )
        if not kubernetes_pod_image:
            raise ValueError("DEMO_KUBERNETES_POD_IMAGE must not be empty")
        ttl = self.redis_default_ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ValueError(
                "DEMO_REDIS_DEFAULT_TTL_SECONDS must be a positive integer"
            )
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "service_label", service_label)
        object.__setattr__(self, "redis_mode", redis_mode)
        object.__setattr__(self, "kubernetes_mode", kubernetes_mode)
        object.__setattr__(self, "kubernetes_namespace", kubernetes_namespace)
        object.__setattr__(self, "kubernetes_pod_image", kubernetes_pod_image)

    @classmethod
    def from_env(cls) -> "DemoConfig":
        redis_mode = os.getenv("DEMO_REDIS_MODE")
        if redis_mode is None or not redis_mode.strip():
            raise ValueError("DEMO_REDIS_MODE is required")
        kubernetes_mode = os.getenv("DEMO_KUBERNETES_MODE")
        if kubernetes_mode is None or not kubernetes_mode.strip():
            raise ValueError("DEMO_KUBERNETES_MODE is required")
        try:
            ttl = int(os.getenv("DEMO_REDIS_DEFAULT_TTL_SECONDS", "300"))
        except ValueError as exc:
            raise ValueError(
                "DEMO_REDIS_DEFAULT_TTL_SECONDS must be a positive integer"
            ) from exc
        config = cls(
            environment=os.getenv("DEMO_ENVIRONMENT", ""),
            service_label=os.getenv("DEMO_SERVICE_LABEL", ""),
            redis_mode=redis_mode,
            redis_default_ttl_seconds=ttl,
            kubernetes_mode=kubernetes_mode,
            kubernetes_namespace=os.getenv("DEMO_KUBERNETES_NAMESPACE", ""),
            kubernetes_pod_image=os.getenv("DEMO_KUBERNETES_POD_IMAGE", ""),
        )
        config.validate_redis_url(
            os.getenv(
                "OPENJIUWEN_SERVICE_REDIS_URL",
                "redis://localhost:6379/0",
            )
        )
        return config

    def validate_redis_url(self, redis_url: str | None) -> None:
        value = (redis_url or "").strip()
        if self.redis_mode == "fake":
            if value.lower() not in _DISABLED_REDIS_URLS:
                raise ValueError(
                    "fake Redis mode requires OPENJIUWEN_SERVICE_REDIS_URL="
                    "disabled or none"
                )
            return

        if value.lower() in _DISABLED_REDIS_URLS or not value:
            raise ValueError("real Redis mode requires OPENJIUWEN_SERVICE_REDIS_URL")
        parsed = urlparse(value)
        network_url = parsed.scheme in {"redis", "rediss"} and parsed.hostname
        unix_url = parsed.scheme == "unix" and parsed.path
        if not (network_url or unix_url):
            raise ValueError("OPENJIUWEN_SERVICE_REDIS_URL must be a valid Redis URL")


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
    environment: str
    service_label: str
    redis_mode: Literal["fake", "real"]


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


def build_demo_system_context(
    service_config: ServiceConfig,
    demo_config: DemoConfig,
) -> SystemContext:
    """Build resources for the selected local or server deployment mode."""
    demo_config.validate_redis_url(service_config.redis_url)
    redis_client = None
    if demo_config.redis_mode == "fake":
        import fakeredis.aioredis

        redis_client = fakeredis.aioredis.FakeRedis()

    if demo_config.kubernetes_mode == "fake":
        kubernetes = FakeKubernetesOperations(demo_config.kubernetes_namespace)
    else:
        kubernetes = KubernetesAsyncioOperations(
            demo_config.kubernetes_namespace,
            kubeconfig=os.getenv("KUBECONFIG"),
        )

    system = build_system_context(
        service_config,
        redis=redis_client,
        kubernetes=kubernetes,
        table_definitions=(DEMO_TABLE,),
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


def register_handlers(application: App, config: DemoConfig) -> None:
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
            existing = await ctx.db_get(DEMO_TABLE_NAME, {"id": request.id})
            if existing is None:
                operation = "created"
                await ctx.db_create(
                    DEMO_TABLE_NAME,
                    {
                        "id": request.id,
                        "value": request.value,
                        "updated_at": updated_at,
                    },
                )
            else:
                operation = "updated"
                await ctx.db_update(
                    DEMO_TABLE_NAME,
                    {"id": request.id},
                    {"value": request.value, "updated_at": updated_at},
                )
            record = await ctx.db_get(DEMO_TABLE_NAME, {"id": request.id})
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
            record = await ctx.db_get(DEMO_TABLE_NAME, {"id": ctx.request.id})
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
            "backend_mode": config.redis_mode,
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
            "backend_mode": config.redis_mode,
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
                "environment": config.environment,
                "service_label": config.service_label,
                "redis_mode": config.redis_mode,
            },
        }

    @application.handle(
        "k8s/pod/read",
        request_model=PodNameInput,
        response_model=PodReadOutput,
        summary="Read a managed Kubernetes Pod",
        description="Read lifecycle fields for one Demo-managed Pod.",
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
            )
        )
        return {"operation": "created", "pod": asdict(pod)}

    @application.handle(
        "k8s/pod/delete",
        request_model=PodNameInput,
        response_model=PodDeleteOutput,
        summary="Delete a managed Kubernetes Pod",
        description="Submit deletion for one Demo-managed Pod.",
        tags=["Kubernetes"],
    )
    async def delete_pod(
        ctx: TypedAppContext[PodNameInput],
        env: Envelope[PodNameInput],
    ) -> dict[str, Any]:
        return asdict(await ctx.kubernetes.delete_pod(ctx.request.name))


def create_app(demo_config: DemoConfig | None = None) -> App:
    config = demo_config or DemoConfig.from_env()

    def create_system_context() -> SystemContext:
        service_config = ServiceConfig.from_env()
        return build_demo_system_context(service_config, config)

    application = App(
        create_system_context,
        title="Simple Service Capabilities Demo",
    )
    register_handlers(application, config)
    return application


app = create_app()
asgi = app.asgi


if __name__ == "__main__":
    app.run()
