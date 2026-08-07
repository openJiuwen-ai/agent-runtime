# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Service resource construction and startup orchestration.

The builders in this module only construct resources. ``bootstrap_system``
starts them through :class:`SystemContext`, which keeps startup ordering and
failure cleanup in one place.
"""

from __future__ import annotations

import socket
from dataclasses import fields
from typing import Any
from uuid import uuid4

from .config import ServiceConfig
from .context.cache.factory import build_cache_backend as _build_cache_backend
from .context.locks.factory import build_lock_backend as _build_lock_backend
from .errors import RedisUnavailable


def coerce_config(
    settings: ServiceConfig | dict[str, Any] | Any | None = None,
) -> ServiceConfig:
    """Normalize a config object while retaining compatibility with mappings."""
    if settings is None:
        return ServiceConfig.from_env()
    if isinstance(settings, ServiceConfig):
        return settings
    if isinstance(settings, dict):
        names = {item.name for item in fields(ServiceConfig)}
        return ServiceConfig(
            **{name: settings[name] for name in names if name in settings}
        )
    values = {
        item.name: getattr(settings, item.name)
        for item in fields(ServiceConfig)
        if hasattr(settings, item.name)
    }
    return ServiceConfig(**values)


def should_bootstrap_db(settings: ServiceConfig) -> bool:
    return settings.db_type != "none"


def should_bootstrap_redis(settings: ServiceConfig) -> bool:
    return bool(
        settings.redis_url and settings.redis_url.lower() not in {"none", "disabled"}
    )


def build_db_handler(settings: ServiceConfig | dict[str, Any] | Any) -> Any | None:
    """Build a foundation DB handler without opening a connection."""
    cfg = coerce_config(settings)
    if cfg.db_type == "none":
        return None
    if cfg.db_type == "sqlite":
        from openjiuwen_runtime.foundation.db import SQLiteHandler

        return SQLiteHandler(cfg.db_name or ":memory:")
    if cfg.db_type == "mysql":
        from openjiuwen_runtime.foundation.db import MySQLHandler

        return MySQLHandler(
            host=cfg.db_host,
            port=cfg.db_port,
            database=cfg.db_name,
            user=cfg.db_user,
            password=cfg.db_password,
        )
    raise ValueError(f"unsupported db_type={cfg.db_type!r}; expected mysql|sqlite|none")


def build_redis_client(settings: ServiceConfig | dict[str, Any] | Any) -> Any | None:
    """Build a Redis client from the configured URL without pinging it."""
    cfg = coerce_config(settings)
    if not should_bootstrap_redis(cfg):
        return None
    try:
        import redis.asyncio

        return redis.asyncio.from_url(cfg.redis_url, decode_responses=False)
    except (
        Exception
    ) as exc:  # pragma: no cover - import failures are environment-specific
        raise RedisUnavailable(f"cannot create Redis client: {exc}") from exc


def build_lock_backend(
    settings: ServiceConfig | str,
    *,
    redis_client: Any = None,
    redis: Any = None,
    etcd_client: Any = None,
    instance_id: str | None = None,
    request_id: str | None = None,
    owns_etcd_client: bool = False,
) -> Any:
    """Select the lock backend once using the configured startup policy."""
    if redis_client is None:
        redis_client = redis
    if isinstance(settings, str):
        return _build_lock_backend(
            settings,
            redis=redis_client,
            etcd_client=etcd_client,
            instance_id=instance_id,
            request_id=request_id,
            owns_etcd_client=owns_etcd_client,
        )
    cfg = coerce_config(settings)
    owns_etcd_client = owns_etcd_client or (
        etcd_client is None and bool(cfg.etcd_endpoints)
    )
    return _build_lock_backend(
        cfg.lock_backend,
        redis=redis_client,
        etcd_client=etcd_client,
        etcd_endpoints=cfg.etcd_endpoints,
        etcd_username=cfg.etcd_username,
        etcd_password=cfg.etcd_password,
        etcd_connect_timeout=cfg.etcd_connect_timeout_seconds,
        etcd_ca_cert=cfg.etcd_ca_cert,
        etcd_cert=cfg.etcd_cert,
        etcd_key=cfg.etcd_key,
        key_prefix=cfg.lock_key_prefix,
        deploy_replicas=cfg.deploy_replicas,
        instance_id=instance_id,
        request_id=request_id,
        owns_etcd_client=owns_etcd_client,
    )


def build_cache_backend(
    settings: ServiceConfig | str,
    *,
    redis_client: Any = None,
    redis: Any = None,
    owns_redis: bool = False,
) -> Any | None:
    """Build the configured memory, Redis, or disabled cache backend."""
    if redis_client is None:
        redis_client = redis
    if isinstance(settings, str):
        return _build_cache_backend(settings, redis=redis_client, owns_redis=owns_redis)
    cfg = coerce_config(settings)
    return _build_cache_backend(
        cfg.cache_backend,
        redis=redis_client,
        key_prefix=cfg.cache_key_prefix,
        default_ttl=cfg.cache_default_ttl_seconds,
        max_entries=cfg.cache_max_entries,
        owns_redis=owns_redis,
    )


def build_system_context(
    settings: ServiceConfig | dict[str, Any] | Any | None = None,
    *,
    db: Any = None,
    redis: Any = None,
    etcd_client: Any = None,
    lock_backend: Any = None,
    cache_backend: Any = None,
    table_definitions: Any = None,
    instance_id: str | None = None,
) -> Any:
    """Construct a :class:`SystemContext` and mark resources it created."""
    from .context.system_context import SystemContext

    cfg = coerce_config(settings)
    resolved_instance_id = instance_id or f"{socket.gethostname()}:{uuid4().hex[:8]}"
    owns_db = db is None and should_bootstrap_db(cfg)
    owns_redis = redis is None and should_bootstrap_redis(cfg)
    db_resource = build_db_handler(cfg) if db is None else db
    redis_resource = build_redis_client(cfg) if redis is None else redis
    lock_resource = lock_backend
    if lock_resource is None:
        lock_resource = build_lock_backend(
            cfg,
            redis_client=redis_resource,
            etcd_client=etcd_client,
            instance_id=resolved_instance_id,
            owns_etcd_client=etcd_client is None and bool(cfg.etcd_endpoints),
        )
    cache_resource = cache_backend
    if cache_resource is None:
        cache_resource = build_cache_backend(cfg, redis_client=redis_resource)
    return SystemContext(
        redis=redis_resource,
        db=db_resource,
        settings=cfg,
        key_prefix=cfg.key_prefix,
        instance_id=resolved_instance_id,
        etcd=etcd_client
        if etcd_client is not None
        else getattr(lock_resource, "_client", None),
        lock_backend=lock_resource,
        cache_backend=cache_resource,
        table_definitions=table_definitions,
        request_timeout_seconds=cfg.request_timeout_seconds,
        _owns_db=owns_db,
        _owns_redis=owns_redis,
        _owns_lock_backend=lock_backend is None,
        _owns_cache_backend=cache_backend is None and cache_resource is not None,
    )


async def bootstrap_system(
    system: Any,
    settings: ServiceConfig | dict[str, Any] | Any | None = None,
    *,
    force: bool = False,
    etcd_client: Any = None,
) -> Any:
    """Attach configured resources to an existing context and start it."""
    cfg = coerce_config(settings if settings is not None else system.settings)
    if force and getattr(system, "_started", False):
        await system.stop()
    system.settings = cfg
    system.key_prefix = cfg.key_prefix
    system.request_timeout_seconds = cfg.request_timeout_seconds
    if force or system.db is None:
        db = build_db_handler(cfg)
        system.set_db(db, owned=db is not None)
    if force or system.redis is None:
        redis = build_redis_client(cfg)
        system.set_redis(redis, owned=redis is not None)
    if force or getattr(system, "lock_backend", None) is None:
        system.set_lock_backend(
            build_lock_backend(
                cfg,
                redis_client=system.redis,
                etcd_client=etcd_client,
                instance_id=system.instance_id,
            )
        )
    if force or getattr(system, "cache_backend", None) is None:
        system.set_cache_backend(build_cache_backend(cfg, redis_client=system.redis))
    await system.start()
    return system


async def shutdown_system(system: Any) -> None:
    await system.stop()


build_redis_handler = build_redis_client
create_system_context = build_system_context


__all__ = [
    "build_cache_backend",
    "build_db_handler",
    "build_lock_backend",
    "build_redis_client",
    "build_redis_handler",
    "build_system_context",
    "create_system_context",
    "bootstrap_system",
    "coerce_config",
    "should_bootstrap_db",
    "should_bootstrap_redis",
    "shutdown_system",
]
