# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""In-memory Kubernetes Pod operations for local development and tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Mapping

from ...errors import ErrorCode, FrameworkError, KubernetesUnavailable
from .base import PodCreateSpec, PodDeleteResult, PodSummary


class FakeKubernetesOperations:
    """Keep managed Pod state in one service process."""

    def __init__(
        self,
        namespace: str,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        self.namespace = namespace
        self.labels = dict(labels or {})
        self._pods: dict[str, PodSummary] = {}
        self._lock: asyncio.Lock | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._lock = asyncio.Lock()
        self._started = True

    async def ping(self) -> bool:
        return self._started

    async def close(self) -> None:
        self._started = False
        self._pods.clear()
        self._lock = None

    async def get_pod(self, name: str) -> PodSummary | None:
        lock = self._require_started()
        async with lock:
            pod = self._pods.get(name)
            return replace(pod) if pod is not None else None

    async def create_pod(self, spec: PodCreateSpec) -> PodSummary:
        lock = self._require_started()
        async with lock:
            if spec.name in self._pods:
                raise FrameworkError(
                    f"pod {spec.name!r} already exists",
                    code=ErrorCode.CONFLICT,
                )
            pod = PodSummary(
                name=spec.name,
                namespace=self.namespace,
                phase="Running",
                ready=True,
                image=spec.image,
            )
            self._pods[spec.name] = pod
            return replace(pod)

    async def delete_pod(self, name: str) -> PodDeleteResult:
        lock = self._require_started()
        async with lock:
            pod = self._pods.pop(name, None)
            state = "delete_requested" if pod is not None else "already_absent"
            return PodDeleteResult(
                name=name,
                namespace=self.namespace,
                state=state,
            )

    def _require_started(self) -> asyncio.Lock:
        if not self._started or self._lock is None:
            raise KubernetesUnavailable("Kubernetes operations are not started")
        return self._lock


__all__ = ["FakeKubernetesOperations"]
