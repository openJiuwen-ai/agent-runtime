# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from typing import Any, Optional

from ..log import get_logger
from .data_store import DataRecord, DataStore

logger = get_logger(__name__)


class _CacheStoreLike:
    async def get_json(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    async def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        raise NotImplementedError

    async def delete(self, *keys: str) -> None:
        raise NotImplementedError


class CacheBackedDataStore(DataStore):
    def __init__(self, *, db_store: DataStore, cache_store: _CacheStoreLike, key_prefix: str = "runtime") -> None:
        self._db = db_store
        self._cache = cache_store
        self._prefix = key_prefix

    def _cache_key(self, namespace: str, key: str) -> str:
        return f"{self._prefix}:{namespace}:{key}"

    async def write(
        self,
        namespace: str,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._db.write(namespace, key, value, ttl_seconds=ttl_seconds, metadata=metadata)
        logger.debug(
            "[DataStore] db_write_ok namespace=%s key=%s ttl=%s value_keys=%s",
            namespace,
            key,
            ttl_seconds,
            sorted(value.keys()) if isinstance(value, dict) else [],
        )
        try:
            # Keep Redis cache payload aligned with direct Redis JSON writes used by runtime.
            await self._cache.set_json(self._cache_key(namespace, key), value, ex=ttl_seconds)
            logger.debug(
                "[DataStore] cache_refresh_ok namespace=%s key=%s ttl=%s",
                namespace,
                key,
                ttl_seconds,
            )
        except Exception as exc:
            logger.warning("[DataStore] cache_refresh_failed namespace=%s key=%s err=%s", namespace, key, exc)

    async def read(self, namespace: str, key: str) -> Optional[DataRecord]:
        cache_key = self._cache_key(namespace, key)
        try:
            cached = await self._cache.get_json(cache_key)
            if isinstance(cached, dict) and isinstance(cached.get("value"), dict):
                # Backward compatibility for historical wrapped cache format.
                logger.debug(
                    "[DataStore] cache_hit_legacy namespace=%s key=%s cache_key=%s",
                    namespace,
                    key,
                    cache_key,
                )
                return DataRecord(
                    namespace=namespace,
                    key=key,
                    value=cached["value"],
                    metadata=cached.get("metadata"),
                )
            if isinstance(cached, dict):
                # Canonical cache format: raw business dict.
                logger.debug(
                    "[DataStore] cache_hit namespace=%s key=%s cache_key=%s",
                    namespace,
                    key,
                    cache_key,
                )
                return DataRecord(
                    namespace=namespace,
                    key=key,
                    value=cached,
                    metadata=None,
                )
            if cached is not None:
                logger.warning(
                    "[DataStore] cache_payload_unexpected namespace=%s key=%s cache_key=%s payload_type=%s",
                    namespace,
                    key,
                    cache_key,
                    type(cached).__name__,
                )
        except Exception as exc:
            logger.warning("[DataStore] cache_read_failed namespace=%s key=%s err=%s", namespace, key, exc)

        logger.debug(
            "[DataStore] cache_miss namespace=%s key=%s cache_key=%s -> fallback_db",
            namespace,
            key,
            cache_key,
        )
        record = await self._db.read(namespace, key)
        if record is None:
            logger.debug("[DataStore] db_miss_after_cache_miss namespace=%s key=%s", namespace, key)
            return None

        logger.debug(
            "[DataStore] db_hit_after_cache_miss namespace=%s key=%s ttl=%s value_keys=%s",
            namespace,
            key,
            record.ttl_seconds,
            sorted(record.value.keys()) if isinstance(record.value, dict) else [],
        )
        try:
            await self._cache.set_json(cache_key, record.value, ex=record.ttl_seconds)
            logger.debug(
                "[DataStore] cache_backfill_ok namespace=%s key=%s ttl=%s",
                namespace,
                key,
                record.ttl_seconds,
            )
        except Exception as exc:
            logger.warning("[DataStore] cache_backfill_failed namespace=%s key=%s err=%s", namespace, key, exc)
        return record

    async def remove(self, namespace: str, key: str) -> None:
        await self._db.remove(namespace, key)
        try:
            await self._cache.delete(self._cache_key(namespace, key))
        except Exception as exc:
            logger.warning("[DataStore] cache_delete_failed namespace=%s key=%s err=%s", namespace, key, exc)
