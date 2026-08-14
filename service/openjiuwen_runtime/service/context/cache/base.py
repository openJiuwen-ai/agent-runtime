# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Cache backend contracts and the request-facing cache client."""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from ...errors import CacheUnavailable

TModel = TypeVar("TModel", bound=BaseModel)


@runtime_checkable
class CacheSerializer(Protocol):
    """Serialize JSON-compatible values for cache storage."""

    def dumps(self, value: Any) -> str:
        raise NotImplementedError

    def loads(self, value: str) -> Any:
        raise NotImplementedError


class JsonCacheSerializer:
    """Compact JSON serializer with native Pydantic model support."""

    @staticmethod
    def dumps(value: Any) -> str:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def loads(value: str) -> Any:
        return json.loads(value)


@dataclass(slots=True)
class CacheMetrics:
    """In-process counters exposed by each cache backend."""

    hits: int = 0
    misses: int = 0
    expirations: int = 0
    evictions: int = 0
    backend_errors: int = 0

    @property
    def expired(self) -> int:
        """Compatibility alias for the number of lazily expired entries."""
        return self.expirations

    @property
    def errors(self) -> int:
        """Compatibility alias for backend operation failures."""
        return self.backend_errors


@runtime_checkable
class CacheBackend(Protocol):
    """String cache operations implemented by local and shared backends."""

    metrics: CacheMetrics

    async def get(self, key: str) -> str | None:
        raise NotImplementedError

    async def set(self, key: str, value: str, ttl: float | None = None) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> bool:
        raise NotImplementedError

    async def exists(self, key: str) -> bool:
        raise NotImplementedError

    async def clear_namespace(self) -> int:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class BaseCacheBackend(ABC):
    """Validation, metrics, and error normalization shared by cache backends."""

    def __init__(
        self,
        *,
        prefix: str,
        default_ttl: float | None = 300,
        max_value_bytes: int = 1024 * 1024,
    ) -> None:
        if not isinstance(prefix, str):
            raise TypeError("prefix must be a string")
        if default_ttl is not None:
            default_ttl = self._validate_ttl(default_ttl)
        if isinstance(max_value_bytes, bool) or int(max_value_bytes) <= 0:
            raise ValueError("max_value_bytes must be a positive integer")
        self.prefix = prefix.rstrip(":")
        self.default_ttl = default_ttl
        self.max_value_bytes = int(max_value_bytes)
        self.metrics = CacheMetrics()
        self._closed = False

    def format_key(self, key: str) -> str:
        key = self._validate_key(key)
        return f"{self.prefix}:{key}" if self.prefix else key

    async def get(self, key: str) -> str | None:
        full_key = self.format_key(key)
        self._ensure_open()
        try:
            value = await self._get(full_key)
        except Exception as exc:  # noqa: BLE001 - normalize backend failures
            self._raise_unavailable("get", exc)
        if value is None:
            self.metrics.misses += 1
        else:
            self.metrics.hits += 1
        return value

    async def set(self, key: str, value: str, ttl: float | None = None) -> None:
        full_key = self.format_key(key)
        value = self._validate_value(value)
        resolved_ttl = self.default_ttl if ttl is None else self._validate_ttl(ttl)
        self._ensure_open()
        try:
            await self._set(full_key, value, resolved_ttl)
        except Exception as exc:  # noqa: BLE001 - normalize backend failures
            self._raise_unavailable("set", exc)

    async def delete(self, key: str) -> bool:
        full_key = self.format_key(key)
        self._ensure_open()
        try:
            return await self._delete(full_key)
        except Exception as exc:  # noqa: BLE001 - normalize backend failures
            self._raise_unavailable("delete", exc)

    async def exists(self, key: str) -> bool:
        full_key = self.format_key(key)
        self._ensure_open()
        try:
            return await self._exists(full_key)
        except Exception as exc:  # noqa: BLE001 - normalize backend failures
            self._raise_unavailable("exists", exc)

    async def clear_namespace(self) -> int:
        self._ensure_open()
        try:
            return await self._clear_namespace()
        except Exception as exc:  # noqa: BLE001 - normalize backend failures
            self._raise_unavailable("clear_namespace", exc)

    async def get_json(
        self,
        key: str,
        default: Any = None,
        *,
        model: type[TModel] | None = None,
    ) -> Any | TModel:
        """Read and deserialize JSON without creating a request facade."""
        return await Cache(self).get_json(key, default=default, model=model)

    async def set_json(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        """Serialize JSON or a Pydantic model and store it."""
        await Cache(self).set_json(key, value, ttl=ttl)

    async def get_model(
        self,
        key: str,
        model: type[TModel],
        default: TModel | None = None,
    ) -> TModel | None:
        """Read a cached object and validate it as a Pydantic model."""
        return await Cache(self).get_model(key, model, default=default)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._close()
        except Exception as exc:  # noqa: BLE001 - normalize backend failures
            self._raise_unavailable("close", exc)

    def _validate_value(self, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("cache values must be strings")
        size = len(value.encode("utf-8"))
        if size > self.max_value_bytes:
            raise ValueError(
                f"cache value is {size} bytes; limit is {self.max_value_bytes} bytes"
            )
        return value

    @staticmethod
    def _validate_key(key: str) -> str:
        if not isinstance(key, str):
            raise TypeError("cache key must be a string")
        if not key:
            raise ValueError("cache key must not be empty")
        return key

    @staticmethod
    def _validate_ttl(ttl: float) -> float:
        ttl = float(ttl)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be a finite positive number")
        return ttl

    def _ensure_open(self) -> None:
        if self._closed:
            raise CacheUnavailable("cache backend is closed")

    def _raise_unavailable(self, operation: str, exc: Exception) -> NoReturn:
        self.metrics.backend_errors += 1
        if isinstance(exc, CacheUnavailable):
            raise exc
        raise CacheUnavailable(f"cache {operation} failed: {exc}") from exc

    @abstractmethod
    async def _get(self, key: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _set(self, key: str, value: str, ttl: float | None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def _delete(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def _exists(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def _clear_namespace(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def _close(self) -> None:
        raise NotImplementedError


class Cache:
    """Request-scoped JSON facade over a process-scoped cache backend."""

    def __init__(
        self,
        backend: CacheBackend,
        *,
        serializer: CacheSerializer | None = None,
    ) -> None:
        if not isinstance(backend, CacheBackend):
            raise TypeError("backend must implement CacheBackend")
        self.backend = backend
        self.serializer = serializer or JsonCacheSerializer()
        self._closed = False

    @property
    def metrics(self) -> CacheMetrics:
        return self.backend.metrics

    async def get(self, key: str) -> str | None:
        self._ensure_open()
        return await self.backend.get(key)

    async def set(self, key: str, value: str, ttl: float | None = None) -> None:
        self._ensure_open()
        await self.backend.set(key, value, ttl=ttl)

    async def delete(self, key: str) -> bool:
        self._ensure_open()
        return await self.backend.delete(key)

    async def exists(self, key: str) -> bool:
        self._ensure_open()
        return await self.backend.exists(key)

    async def clear_namespace(self) -> int:
        self._ensure_open()
        return await self.backend.clear_namespace()

    async def get_json(
        self,
        key: str,
        default: Any = None,
        *,
        model: type[TModel] | None = None,
    ) -> Any | TModel:
        raw = await self.get(key)
        if raw is None:
            return default
        try:
            value = self.serializer.loads(raw)
            return model.model_validate(value) if model is not None else value
        except Exception as exc:  # noqa: BLE001 - expose one cache-facing error
            raise CacheUnavailable(f"cache value for {key!r} is invalid") from exc

    async def set_json(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        try:
            serialized = self.serializer.dumps(value)
        except Exception as exc:  # noqa: BLE001 - expose one cache-facing error
            raise CacheUnavailable(
                f"cache value for {key!r} is not serializable"
            ) from exc
        await self.set(key, serialized, ttl=ttl)

    async def get_model(
        self,
        key: str,
        model: type[TModel],
        default: TModel | None = None,
    ) -> TModel | None:
        return await self.get_json(key, default=default, model=model)

    async def close(self) -> None:
        """Close this request facade without closing the shared backend."""
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise CacheUnavailable("request cache client is closed")


__all__ = [
    "BaseCacheBackend",
    "Cache",
    "CacheBackend",
    "CacheMetrics",
    "CacheSerializer",
    "JsonCacheSerializer",
]
