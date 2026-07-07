# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class DataRecord:
    namespace: str
    key: str
    value: dict[str, Any]
    version: int = 1
    ttl_seconds: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DataStore(ABC):
    @abstractmethod
    async def write(
        self,
        namespace: str,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def read(self, namespace: str, key: str) -> Optional[DataRecord]:
        raise NotImplementedError

    @abstractmethod
    async def remove(self, namespace: str, key: str) -> None:
        raise NotImplementedError
