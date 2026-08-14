# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Lock backend selection used during service startup."""

from __future__ import annotations

from typing import Any

from ...errors import LockBackendUnavailable
from .base import LockBackend
from .backends.etcd import EtcdLockBackend, create_etcd_client
from .backends.memory import MemoryLockBackend
from .backends.redis import RedisLockBackend


def build_lock_backend(
    backend: str | Any = "auto",
    *,
    redis: Any = None,
    etcd_client: Any = None,
    etcd_endpoints: str | list[str] | tuple[str, ...] | None = None,
    etcd_username: str | None = None,
    etcd_password: str | None = None,
    etcd_connect_timeout: float | None = None,
    etcd_ca_cert: str | None = None,
    etcd_cert: str | None = None,
    etcd_key: str | None = None,
    key_prefix: str = "service:lock",
    deploy_replicas: int = 1,
    instance_id: str | None = None,
    request_id: str | None = None,
    owns_etcd_client: bool = False,
) -> LockBackend:
    """Build one lock backend and fail fast when its prerequisites are absent."""
    if not isinstance(backend, str):
        config = backend
        return build_lock_backend(
            getattr(config, "lock_backend", "auto"),
            redis=redis,
            etcd_client=etcd_client,
            etcd_endpoints=getattr(config, "etcd_endpoints", etcd_endpoints),
            etcd_username=getattr(config, "etcd_username", etcd_username),
            etcd_password=getattr(config, "etcd_password", etcd_password),
            etcd_connect_timeout=getattr(
                config, "etcd_connect_timeout_seconds", etcd_connect_timeout
            ),
            etcd_ca_cert=getattr(config, "etcd_ca_cert", etcd_ca_cert),
            etcd_cert=getattr(config, "etcd_cert", etcd_cert),
            etcd_key=getattr(config, "etcd_key", etcd_key),
            key_prefix=getattr(config, "lock_key_prefix", key_prefix),
            deploy_replicas=getattr(config, "deploy_replicas", deploy_replicas),
            instance_id=instance_id,
            request_id=request_id,
            owns_etcd_client=owns_etcd_client,
        )
    selected = str(backend or "auto").strip().lower()
    if selected not in {"auto", "memory", "redis", "etcd"}:
        raise ValueError("lock backend must be one of auto, memory, redis, etcd")
    if selected == "auto":
        if etcd_client is not None or etcd_endpoints:
            selected = "etcd"
        elif redis is not None:
            selected = "redis"
        elif int(deploy_replicas) == 1:
            selected = "memory"
        else:
            raise LockBackendUnavailable(
                "multi-replica deployment requires an etcd or Redis lock backend"
            )
    if selected == "memory":
        return MemoryLockBackend(prefix=key_prefix)
    if selected == "redis":
        if redis is None:
            raise LockBackendUnavailable("Redis lock backend requires a Redis client")
        return RedisLockBackend(
            redis,
            prefix=key_prefix,
            instance_id=instance_id,
            request_id=request_id,
        )
    if etcd_client is None:
        if not etcd_endpoints:
            raise LockBackendUnavailable(
                "etcd lock backend requires a client or endpoint"
            )
        etcd_client = create_etcd_client(
            etcd_endpoints,
            username=etcd_username,
            password=etcd_password,
            connect_timeout=etcd_connect_timeout,
            tls_ca_cert=etcd_ca_cert,
            tls_cert=etcd_cert,
            tls_key=etcd_key,
        )
        owns_etcd_client = True
    return EtcdLockBackend(
        etcd_client,
        prefix=key_prefix,
        owns_client=owns_etcd_client,
        instance_id=instance_id,
        request_id=request_id,
    )


class LockBackendFactory:
    """Object-oriented facade for integrations that prefer a factory type."""

    @staticmethod
    def build(*args: Any, **kwargs: Any) -> LockBackend:
        return build_lock_backend(*args, **kwargs)


create_lock_backend = build_lock_backend

__all__ = ["LockBackendFactory", "build_lock_backend", "create_lock_backend"]
