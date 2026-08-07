# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Cache backend construction helpers."""

from __future__ import annotations

from typing import Any

from ...errors import CacheUnavailable
from .base import CacheBackend
from .memory import MemoryCacheBackend
from .redis import RedisCacheBackend


def build_cache_backend(
    backend: str | Any = "memory",
    *,
    redis: Any = None,
    key_prefix: str = "service:cache",
    default_ttl: float | None = 300,
    max_entries: int = 1000,
    max_value_bytes: int = 1024 * 1024,
    owns_redis: bool = False,
) -> CacheBackend | None:
    """Build a local or Redis cache backend from an explicit selection."""
    if not isinstance(backend, str):
        config = backend
        return build_cache_backend(
            getattr(config, "cache_backend", "memory"),
            redis=redis,
            key_prefix=getattr(config, "cache_key_prefix", key_prefix),
            default_ttl=getattr(config, "cache_default_ttl_seconds", default_ttl),
            max_entries=getattr(config, "cache_max_entries", max_entries),
            max_value_bytes=max_value_bytes,
            owns_redis=owns_redis,
        )
    selected = str(backend or "none").strip().lower()
    if selected == "none":
        return None
    if selected == "memory":
        return MemoryCacheBackend(
            prefix=key_prefix,
            default_ttl=default_ttl,
            max_entries=max_entries,
            max_value_bytes=max_value_bytes,
        )
    if selected == "redis":
        if redis is None:
            raise CacheUnavailable("Redis cache backend requires a Redis client")
        return RedisCacheBackend(
            redis,
            prefix=key_prefix,
            default_ttl=default_ttl,
            max_value_bytes=max_value_bytes,
            owns_redis=owns_redis,
        )
    raise ValueError("cache backend must be one of memory, redis, none")


class CacheBackendFactory:
    """Object-oriented facade for cache backend construction."""

    @staticmethod
    def build(*args: Any, **kwargs: Any) -> CacheBackend | None:
        return build_cache_backend(*args, **kwargs)


create_cache_backend = build_cache_backend

__all__ = ["CacheBackendFactory", "build_cache_backend", "create_cache_backend"]
