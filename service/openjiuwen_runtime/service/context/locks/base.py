# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""锁后端与凭证的稳定契约。"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LockCapabilities:
    """声明锁后端提供的跨副本和 fencing 能力。"""

    distributed: bool
    fencing: bool


@dataclass(frozen=True, slots=True)
class LockCredential:
    """一次成功获取对应的不可变锁凭证。"""

    key: str
    token: str
    backend: str
    lease_id: str | int | None
    fencing_token: int | None
    acquired_at: float
    expires_at: float

    def renewed(
        self, ttl: float, *, lease_id: str | int | None = None
    ) -> "LockCredential":
        """按单调时钟生成续约后的凭证副本。"""
        return replace(
            self,
            lease_id=self.lease_id if lease_id is None else lease_id,
            expires_at=time.monotonic() + ttl,
        )


@runtime_checkable
class LockBackend(Protocol):
    """单次原子锁操作；等待和续约循环由 ``LockManager`` 统一实现。"""

    capabilities: LockCapabilities

    async def try_acquire(self, key: str, ttl: float) -> LockCredential | None:
        raise NotImplementedError

    async def renew(self, credential: LockCredential, ttl: float) -> LockCredential:
        raise NotImplementedError

    async def release(self, credential: LockCredential) -> bool:
        raise NotImplementedError


__all__ = ["LockBackend", "LockCapabilities", "LockCredential"]
