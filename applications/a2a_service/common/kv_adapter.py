# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""通用 namespace/key/value 适配层，将 DataStore 抽象为简单的 kv 读写模型。"""
from __future__ import annotations

from typing import Any, Optional, Protocol


class _DataStoreLike(Protocol):
    async def write(
        self,
        namespace: str,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        ...

    async def read(self, namespace: str, key: str) -> Optional[Any]:
        ...

    async def remove(self, namespace: str, key: str) -> None:
        ...


class KvAdapter:
    """通用 namespace/key/value 读写适配，不绑定任何业务类型。

    使用方式：
        adapter = KvAdapter(data_store, namespace="session_task", default_ttl_seconds=1800)
        await adapter.put("conv-123", {"task_id": "abc"})
        data = await adapter.get("conv-123")
        await adapter.delete("conv-123")
    """

    def __init__(
        self,
        data_store: _DataStoreLike,
        *,
        namespace: str,
        default_ttl_seconds: int = 1800,
    ) -> None:
        self._store = data_store
        self._namespace = namespace
        self._default_ttl_seconds = default_ttl_seconds

    @property
    def namespace(self) -> str:
        return self._namespace

    async def put(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        await self._store.write(
            self._namespace,
            key,
            value,
            ttl_seconds=self._default_ttl_seconds if ttl_seconds is None else ttl_seconds,
            metadata=metadata,
        )

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        record = await self._store.read(self._namespace, key)
        return None if record is None else record.value

    async def delete(self, key: str) -> None:
        await self._store.remove(self._namespace, key)
