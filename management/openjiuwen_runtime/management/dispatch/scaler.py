# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""K8s scaler controller for dispatch."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from .config import DispatchSettings
from .models import PodInfo, PodState, SessionState
from .store import RedisDispatchStore


class ScalerController:
    """Consume scale events, watch pods, and shrink idle capacity safely."""

    def __init__(
        self,
        store: RedisDispatchStore,
        settings: DispatchSettings,
        core_api: Any | None = None,
        apps_api: Any | None = None,
        watch_factory: Any | None = None,
        time_fn: Any = time.time,
    ):
        self.store = store
        self.settings = settings
        self.core_api = core_api
        self.apps_api = apps_api
        self.watch_factory = watch_factory
        self.time_fn = time_fn
        self.admin_cursor: str | None = None
        self.scale_cursor: str | None = None
        self.active_pod_hash: str | None = None
        self._active_hash_created_at = 0.0

    async def init(self) -> None:
        self.settings = self.settings.apply_runtime_overrides(await self.store.load_config())
        await self._ensure_clients()
        if self.admin_cursor is None:
            self.admin_cursor = await self.store.load_admin_cursor()
        if self.scale_cursor is None:
            self.scale_cursor = await self.store.load_scale_cursor()

    async def run(self) -> None:
        await self.init()
        await asyncio.gather(
            self._watch_loop(),
            self._event_loop(),
            self._sweep_loop(),
            self._prewarm_loop(),
        )

    async def sweep_once(self) -> None:
        now = self.time_fn()
        sessions = await self.store.list_sessions()
        for session in sessions:
            if session.state == SessionState.TTL_WAITING and session.expire_at and session.expire_at <= now:
                await self.store.release_session_capacity(session)

        pods = await self.store.all_pods()
        current_total = len(pods)
        for pod in self._drainable_sorted(pods, now):
            if current_total <= self.settings.min_instance:
                break
            await self._drain_and_kill(pod)
            current_total -= 1

        await self._ensure_min_idle()

    async def handle_pod_event(self, event_type: str, pod_obj: Any) -> None:
        pod_id = pod_obj.metadata.name
        if event_type == "DELETED":
            victim = await self.store.get_pod(pod_id)
            if victim and (victim.state != PodState.DRAINING or victim.bound_sessions):
                for session_id in victim.bound_sessions:
                    await self.store.mark_session_orphaned(session_id)
                await self.store.enqueue_admin_event("compensate_mis_deletion", pod_id=pod_id)
            await self.store.remove_pod(pod_id)
            return

        if not self._is_ready(pod_obj) or not getattr(pod_obj.status, "pod_ip", None):
            return

        created_at = (
            pod_obj.metadata.creation_timestamp.timestamp()
            if getattr(pod_obj.metadata, "creation_timestamp", None)
            else self.time_fn()
        )
        pod_template_hash = self._extract_pod_template_hash(pod_obj)
        if pod_template_hash and created_at >= self._active_hash_created_at:
            self.active_pod_hash = pod_template_hash
            self._active_hash_created_at = created_at

        existing = await self.store.get_pod(pod_id)
        bound_sessions = list(existing.bound_sessions) if existing else []
        idle_since = self.time_fn() if not existing and not bound_sessions else None
        if existing and not bound_sessions:
            idle_since = existing.idle_since if existing.idle_since is not None else self.time_fn()

        pod = PodInfo(
            pod_id=pod_id,
            pod_ip=pod_obj.status.pod_ip,
            port=self.settings.agent_port,
            capacity=self.settings.concurrent_num,
            allocated=existing.allocated if existing else 0,
            state=PodState.SERVING if not existing or existing.allocated < self.settings.concurrent_num else PodState.FULL,
            bound_sessions=bound_sessions,
            idle_since=idle_since,
            created_at=created_at,
            pod_template_hash=pod_template_hash or (existing.pod_template_hash if existing else None),
        )
        await self.store.save_pod(pod)
        if existing is None:
            await self.store.publish_pod_ready(pod_id)

    async def _event_loop(self) -> None:
        while True:
            await self._event_loop_once()

    async def _event_loop_once(self) -> bool:
        if self.admin_cursor is None:
            self.admin_cursor = await self.store.load_admin_cursor()
        if self.scale_cursor is None:
            self.scale_cursor = await self.store.load_scale_cursor()

        admin = await self.store.consume_admin_events(self.admin_cursor, block_ms=0, count=16)
        if admin:
            for msg_id, fields in admin:
                await self._handle_admin(msg_id, fields)
                self.admin_cursor = msg_id
                await self.store.save_admin_cursor(msg_id)
            return True

        scale = await self.store.consume_scale_events(
            self.scale_cursor,
            block_ms=max(0, int(self.settings.scale_up_debounce * 1000)),
            count=64,
        )
        if not scale:
            return False

        for aggregated in self._dedupe_and_sum(scale):
            await self._handle_scale(aggregated)
            self.scale_cursor = aggregated["id"]
            await self.store.save_scale_cursor(aggregated["id"])
        return True

    async def _handle_admin(self, msg_id: str, fields: dict[str, str]) -> None:
        reason = fields.get("reason", "")
        if reason == "compensate_mis_deletion":
            await self._scale_up_by(1)
            return
        if reason == "prewarm_consumed":
            await self._ensure_min_idle()
            return
        if reason == "config_update":
            self.settings = self.settings.apply_runtime_overrides(await self.store.load_config())
            return
        if reason == "manual_scale":
            count = int(fields.get("count", "0") or 0)
            if count > 0:
                await self._scale_up_by(count)
            return
        # "prewarm" and unknown admin events are intentionally no-ops here. They are
        # persisted for observability / future handlers but don't require side effects.
        _ = msg_id

    async def _handle_scale(self, event: dict[str, int | str]) -> None:
        if event.get("reason") != "no_capacity":
            return
        total_concurrency = int(event.get("concurrency", 0) or 0)
        demand = int(event.get("demand", 0) or 0)
        by_concurrency = math.ceil(total_concurrency / self.settings.concurrent_num) if total_concurrency > 0 else 0
        await self._scale_up_by(max(1, demand, by_concurrency))

    @staticmethod
    def _dedupe_and_sum(events: list[tuple[str, dict[str, str]]]) -> list[dict[str, int | str]]:
        aggregated: dict[str, dict[str, int | str]] = {}
        for msg_id, fields in events:
            reason = fields.get("reason", "no_capacity")
            current = aggregated.setdefault(
                reason,
                {"id": msg_id, "reason": reason, "demand": 0, "concurrency": 0},
            )
            current["id"] = msg_id
            current["demand"] = int(current["demand"]) + int(fields.get("demand", "1") or 1)
            current["concurrency"] = int(current["concurrency"]) + int(fields.get("concurrency", "0") or 0)
        return list(aggregated.values())

    async def _watch_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._resync_once()
                await self._watch_stream()
                backoff = 1.0
            except Exception:
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)

    async def _sweep_loop(self) -> None:
        while True:
            await self.sweep_once()
            await asyncio.sleep(self.settings.sweep_interval)

    async def _prewarm_loop(self) -> None:
        while True:
            await self._ensure_min_idle()
            await asyncio.sleep(self.settings.prewarm_check_interval)

    async def _ensure_clients(self) -> None:
        if self.core_api is not None and self.apps_api is not None and self.watch_factory is not None:
            return

        try:
            from kubernetes_asyncio import client, config, watch
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on runtime deps
            raise ModuleNotFoundError("kubernetes-asyncio is required to run the scaler controller") from exc

        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()

        api_client = client.ApiClient()
        self.core_api = client.CoreV1Api(api_client)
        self.apps_api = client.AppsV1Api(api_client)
        self.watch_factory = watch.Watch

    async def _resync_once(self) -> None:
        pod_list = await self.core_api.list_namespaced_pod(
            namespace=self.settings.namespace,
            label_selector=self.settings.pod_label_selector,
        )
        live_ids: set[str] = set()
        for pod_obj in getattr(pod_list, "items", []):
            if self._is_ready(pod_obj) and getattr(pod_obj.status, "pod_ip", None):
                live_ids.add(pod_obj.metadata.name)
                await self.handle_pod_event("READY", pod_obj)

        for pod_id in await self.store.all_pod_ids():
            if pod_id not in live_ids:
                await self.store.remove_pod(pod_id)

        self._resource_version = getattr(getattr(pod_list, "metadata", None), "resource_version", None)

    async def _watch_stream(self) -> None:
        watcher = self.watch_factory()
        async with watcher.stream(
            self.core_api.list_namespaced_pod,
            namespace=self.settings.namespace,
            label_selector=self.settings.pod_label_selector,
            resource_version=self._resource_version,
        ) as stream:
            async for event in stream:
                pod_obj = event["object"]
                await self.handle_pod_event(event["type"], pod_obj)

    async def _scale_up_by(self, count: int) -> int:
        if count <= 0:
            return 0
        deployment = await self.apps_api.read_namespaced_deployment(
            name=self.settings.deployment_name,
            namespace=self.settings.namespace,
        )
        replicas = int(getattr(getattr(deployment, "spec", None), "replicas", 0) or 0)
        live_pods = len(await self.store.all_pods())
        desired = min(self.settings.max_instance, max(replicas, live_pods) + count)
        if desired <= replicas:
            return 0
        await self.apps_api.patch_namespaced_deployment(
            name=self.settings.deployment_name,
            namespace=self.settings.namespace,
            body={"spec": {"replicas": desired}},
        )
        return desired - replicas

    async def _ensure_min_idle(self) -> int:
        if self.settings.min_idle <= 0:
            return 0
        pods = await self.store.all_pods()
        idle_count = sum(
            1 for pod in pods
            if pod.state == PodState.SERVING
            and len(pod.bound_sessions) == 0
            and self._is_active_version(pod)
        )
        total = len(pods)
        if idle_count >= self.settings.min_idle or total >= self.settings.max_instance:
            return 0

        shortage = min(self.settings.min_idle - idle_count, self.settings.max_instance - total)
        applied = await self._scale_up_by(shortage)
        if applied > 0:
            await self.store.enqueue_admin_event("prewarm", count=applied)
        return applied

    def _drainable_sorted(self, pods: list[PodInfo], now: float) -> list[PodInfo]:
        candidates = [
            pod for pod in pods
            if len(pod.bound_sessions) == 0
            and pod.idle_since is not None
            and (now - pod.idle_since) >= self.settings.idle_timeout
        ]
        ordered = sorted(
            candidates,
            key=lambda pod: (
                0 if not self._is_active_version(pod) else 1,
                pod.idle_since or 0.0,
            ),
        )
        keep = self.settings.min_idle
        result: list[PodInfo] = []
        for pod in ordered:
            if keep > 0 and self._is_active_version(pod):
                keep -= 1
                continue
            result.append(pod)
        return result

    async def _drain_and_kill(self, pod: PodInfo) -> None:
        draining = pod.model_copy(update={"state": PodState.DRAINING})
        await self.store.save_pod(draining)
        await self.core_api.patch_namespaced_pod(
            name=pod.pod_id,
            namespace=self.settings.namespace,
            body={
                "metadata": {
                    "annotations": {
                        "controller.kubernetes.io/pod-deletion-cost": "-100",
                    }
                }
            },
        )
        deployment = await self.apps_api.read_namespaced_deployment(
            name=self.settings.deployment_name,
            namespace=self.settings.namespace,
        )
        replicas = int(getattr(getattr(deployment, "spec", None), "replicas", 0) or 0)
        if replicas <= self.settings.min_instance:
            return
        await self.apps_api.patch_namespaced_deployment(
            name=self.settings.deployment_name,
            namespace=self.settings.namespace,
            body={"spec": {"replicas": replicas - 1}},
        )

    def _is_active_version(self, pod: PodInfo) -> bool:
        return not self.active_pod_hash or not pod.pod_template_hash or pod.pod_template_hash == self.active_pod_hash

    @staticmethod
    def _extract_pod_template_hash(pod_obj: Any) -> str | None:
        labels = getattr(getattr(pod_obj, "metadata", None), "labels", {}) or {}
        return labels.get("pod-template-hash")

    @staticmethod
    def _is_ready(pod_obj: Any) -> bool:
        for condition in getattr(getattr(pod_obj, "status", None), "conditions", []) or []:
            if getattr(condition, "type", None) == "Ready" and getattr(condition, "status", None) == "True":
                return True
        return False
