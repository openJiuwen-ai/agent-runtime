# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol

from openjiuwen_runtime.foundation.log import get_logger
from .data_store import DataRecord, DataStore

logger = get_logger(__name__)


class _RecordLike(Protocol):
    def to_dict(self) -> dict[str, Any]:
        ...


class _DbHandlerLike(Protocol):
    async def get(self, table_name: str, filters: dict[str, Any]) -> Optional[_RecordLike]:
        ...

    async def create(self, table_name: str, data: dict[str, Any]) -> Any:
        ...

    async def update(self, table_name: str, filters: dict[str, Any], data: dict[str, Any]) -> Any:
        ...

    async def delete(self, table_name: str, filters: dict[str, Any]) -> bool:
        ...


class DbDataStore(DataStore):
    def __init__(self, db_handler: _DbHandlerLike, *, table_name: str = "runtime_kv_state") -> None:
        self._db = db_handler
        self._table = table_name

    @staticmethod
    def _filters(namespace: str, key: str) -> dict[str, Any]:
        return {"state_domain": namespace, "state_key": key}

    @staticmethod
    def _normalize_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = payload.decode("utf-8")
            except Exception:
                return {"value": str(payload)}
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return {}
            try:
                loaded = json.loads(text)
                if isinstance(loaded, dict):
                    return loaded
                return {"value": loaded}
            except Exception:
                return {"value": payload}
        if payload is None:
            return {}
        return {"value": payload}

    @staticmethod
    def _to_record(row: _RecordLike) -> DataRecord:
        data = row.to_dict() if hasattr(row, "to_dict") else dict(row)
        payload = DbDataStore._normalize_payload(data.get("payload"))
        ttl_seconds = None
        expire_at = data.get("expire_at")
        if isinstance(expire_at, datetime):
            ttl_seconds = max(int((expire_at - datetime.utcnow()).total_seconds()), 0)
        return DataRecord(
            namespace=str(data.get("state_domain") or ""),
            key=str(data.get("state_key") or ""),
            value=payload,
            version=int(data.get("version") or 1),
            ttl_seconds=ttl_seconds,
            metadata=data.get("state_metadata"),
            updated_at=data.get("updated_at"),
        )

    async def write(
        self,
        namespace: str,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.utcnow()
        expire_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds else None
        filters = self._filters(namespace, key)
        logger.debug(
            "[DbDataStore] write_begin namespace=%s key=%s ttl=%s expire_at=%s",
            namespace,
            key,
            ttl_seconds,
            expire_at,
        )

        try:
            await self._db.create(
                self._table,
                {
                    "state_domain": namespace,
                    "state_key": key,
                    "payload": value,
                    "version": 1,
                    "state_metadata": metadata,
                    "updated_at": now,
                    "expire_at": expire_at,
                },
            )
            logger.debug("[DbDataStore] write_insert namespace=%s key=%s", namespace, key)
            return
        except Exception as e:
            logger.debug("[DbDataStore] create_failed fallback to update: %s", e)

        existing = await self._db.get(self._table, filters)
        current = existing.to_dict() if hasattr(existing, "to_dict") else {}
        await self._db.update(
            self._table,
            filters,
            {
                "payload": value,
                "version": int(current.get("version") or 1) + 1,
                "state_metadata": metadata,
                "updated_at": now,
                "expire_at": expire_at,
            },
        )
        logger.debug("[DbDataStore] write_update namespace=%s key=%s", namespace, key)

    async def read(self, namespace: str, key: str) -> Optional[DataRecord]:
        row = await self._db.get(self._table, self._filters(namespace, key))
        if row is None:
            return None
        return self._to_record(row)

    async def remove(self, namespace: str, key: str) -> None:
        await self._db.delete(self._table, self._filters(namespace, key))
