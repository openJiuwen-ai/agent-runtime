# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Kubernetes Pod capability contracts and service-domain models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class PodCreateSpec:
    """Restricted input used to create one managed Pod."""

    name: str
    image: str


@dataclass(frozen=True)
class PodSummary:
    """Stable Pod lifecycle fields exposed to service handlers."""

    name: str
    namespace: str
    phase: str
    ready: bool
    image: str | None


@dataclass(frozen=True)
class PodDeleteResult:
    """Result of submitting or observing a Pod deletion."""

    name: str
    namespace: str
    state: Literal[
        "delete_requested",
        "deletion_in_progress",
        "already_absent",
    ]


@runtime_checkable
class KubernetesOperations(Protocol):
    """Process-level asynchronous operations for managed Pods."""

    async def start(self) -> None:
        ...

    async def ping(self) -> bool:
        ...

    async def close(self) -> None:
        ...

    async def get_pod(self, name: str) -> PodSummary | None:
        ...

    async def create_pod(self, spec: PodCreateSpec) -> PodSummary:
        ...

    async def delete_pod(self, name: str) -> PodDeleteResult:
        ...


__all__ = [
    "KubernetesOperations",
    "PodCreateSpec",
    "PodDeleteResult",
    "PodSummary",
]
