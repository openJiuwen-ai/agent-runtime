# coding: utf-8
"""RM 后台任务（RM 设计 §5.4）：autoscale / reclaim / 死Pod+健康探测 / 孤儿对账。

每个任务一个 ``*_once`` 方法（自身带 tick 级选主锁），调度由 main 经
SystemContext.create_single_leader_job 注入。均 per-scope 操作，不读 SM 模块
Redis key（跨模块数据只走 Facade）：

- autoscale（场景 H）：idle < min_idle_pods 且未达 max_pods → 占位 deploy 热备。
- reclaim（场景 K）：idle 池 excess（超 min_idle 底数）中 aged ≥ pod_ttl →
  K8s delete + PURGE + notify_pod_dead。安全性靠 SM 侧 ZREM 契约，不读 SM。
- watch（场景 J/N，10s）：死 Pod 轮询（判死枚举）+ AgentServer /health 健康探测
  （连续 2 次失败判半死）→ 按死 Pod 清理。
- reconcile（场景 L，30s）：Redis↔K8s 孤儿对账 + RM↔SM stale Pod 对账（经 Facade）。
"""

from __future__ import annotations

import json
import logging
import time
from uuid import uuid4

from ..util import now_ts, to_int
from .k8s import K8sPodClient
from .models import DEAD_POD_STATUSES, POD_LABEL_SELECTOR
from .orchestrator import ResourceOrchestrator, _deploy_ver
from .state import ResourceState

logger = logging.getLogger("agent_runtime.resource_manager")

HEALTH_FAIL_THRESHOLD = 2   # 连续失败判半死（防瞬时抖动误杀，场景 N）
WATCH_LOCK_TTL = 15         # 10s tick + 余量
RECONCILE_LOCK_TTL = 60     # 30s tick + 余量


class ResourceSweeper:
    """RM 自治任务集（业务状态在 Redis；进程内仅存诊断去重集 _probe_gap_warned）。"""

    def __init__(
        self,
        rm_state: ResourceState,
        k8s: K8sPodClient,
        sm_facade,                    # SessionManagerFacade（进程内）
        *,
        orchestrator: ResourceOrchestrator | None = None,
        health_fail_threshold: int = HEALTH_FAIL_THRESHOLD,
    ) -> None:
        self.state = rm_state
        self.k8s = k8s
        self.sm = sm_facade
        self.orchestrator = orchestrator or ResourceOrchestrator(rm_state, k8s)
        self.health_fail_threshold = health_fail_threshold
        # 数据缺失告警去重（仅影响日志，不参与业务判定）
        self._probe_gap_warned: set[str] = set()

    # -------------------------------------------------------------- autoscale（H）

    async def autoscale_once(self) -> None:
        token = uuid4().hex
        if not await self.state.try_lock(self.state.k.lock_autoscale(), 2, token):
            return
        t0 = time.monotonic()
        outcomes: dict[str, int] = {}
        try:
            for scope_id in await self.state.known_scope_ids():
                outcome = await self._autoscale_scope(scope_id)
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
        finally:
            await self.state.unlock(self.state.k.lock_autoscale(), token)
        logger.debug(
            "autoscale tick: scopes=%d %s duration_ms=%.0f",
            sum(outcomes.values()),
            " ".join(f"{k}={v}" for k, v in sorted(outcomes.items())),
            (time.monotonic() - t0) * 1000,
        )

    async def _autoscale_scope(self, scope_id: str) -> str:
        """单 scope 补位判定；返回结果标签（聚合进 tick 汇总日志）。"""
        cfg = await self.state.load_scope_config(scope_id)
        min_idle = to_int(cfg.get("min_idle_pods"))
        max_pods = to_int(cfg.get("max_pods"), 1)
        if min_idle <= 0:
            return "skip_min_idle0"
        idle = await self.state.idle_pods(scope_id)
        if len(idle) >= min_idle:
            return "skip_warm"
        total = await self.state.pod_count(scope_id) + await self.state.deploying_count(scope_id)
        if total >= max_pods:
            return "skip_max"
        # 热备 deploy 用缓存的 pod_spec（config_sync A 类变更后为新值）
        try:
            pod_spec = json.loads(cfg.get("pod_spec_json") or "{}")
        except ValueError:
            logger.warning("autoscale: scope=%s has invalid pod_spec_json, skip", scope_id)
            return "skip_bad_spec"
        if not pod_spec:
            return "skip_no_spec"
        deploy_ver = cfg.get("deploy_ver") or _deploy_ver(pod_spec)
        lock_key = self.state.k.lock_deploy(scope_id)
        lock_token = f"autoscale-{uuid4().hex}"
        token = f"warm-{uuid4().hex}"
        # 占位（计入 max_pods，不碰 idle 池——补位不该消耗既有暖 Pod）→
        # 选主 deploy → REGISTER(idle_flag=True)
        action = await self.state.deploy_placeholder(scope_id, token)
        if action != "need_deploy":
            await self.state.clear_deploy_token(scope_id, token)
            return f"skip_{action}"
        if not await self.state.try_lock(lock_key, 360, lock_token):
            await self.state.clear_deploy_token(scope_id, token)
            return "skip_lock_busy"
        try:
            await self.orchestrator._deploy_and_register(
                scope_id, pod_spec, deploy_ver, token, idle_flag=True
            )
        except Exception:  # noqa: BLE001 - sweeper 自愈路径，记录不中断
            logger.exception("autoscale deploy failed: scope=%s", scope_id)
            return "deploy_error"
        finally:
            await self.state.unlock(lock_key, lock_token)
        return "deployed"

    # -------------------------------------------------------------- reclaim（K）

    async def reclaim_once(self) -> None:
        token = uuid4().hex
        if not await self.state.try_lock(self.state.k.lock_reclaim(), 2, token):
            return
        t0 = time.monotonic()
        scopes = reclaimed = 0
        try:
            now = now_ts()
            for scope_id in await self.state.known_scope_ids():
                scopes += 1
                cfg = await self.state.load_scope_config(scope_id)
                min_idle = to_int(cfg.get("min_idle_pods"))
                pod_ttl = to_int(cfg.get("pod_ttl"), 300)
                idle = await self.state.idle_pods(scope_id)
                if len(idle) <= min_idle:
                    continue
                # 保护最早转入 idle 的 min_idle 个（保底热备），回收 excess
                aged = {p: await self.state.idle_since(p) for p in idle}
                ranked = sorted(idle, key=lambda p: aged[p])
                excess = ranked[min_idle:]
                for pod_id in excess:
                    if aged[pod_id] and now - aged[pod_id] >= pod_ttl:
                        await self._reclaim_pod(pod_id, scope_id)
                        reclaimed += 1
        finally:
            await self.state.unlock(self.state.k.lock_reclaim(), token)
        logger.debug("reclaim tick: scopes=%d reclaimed=%d duration_ms=%.0f",
                     scopes, reclaimed, (time.monotonic() - t0) * 1000)

    async def _reclaim_pod(self, pod_id: str, scope_id: str) -> None:
        logger.info("reclaim idle pod: scope=%s pod=%s", scope_id, pod_id)
        await self._purge_and_notify(pod_id)

    # -------------------------------------------------------------- watch（J/N）

    async def watch_once(self) -> None:
        """死 Pod 轮询 + 健康探测（同把选主锁，10s tick）。"""
        token = uuid4().hex
        if not await self.state.try_lock(self.state.k.lock_watch(), WATCH_LOCK_TTL, token):
            return
        t0 = time.monotonic()
        pods = dead = purged = 0
        try:
            for pod_id in await self.state.all_pod_ids():
                pods += 1
                info = await self.state.pod_info(pod_id)
                if not info:
                    continue
                pod = await self.k8s.get_pod(pod_id, info.get("namespace", "default"))
                if pod is None or pod.phase in DEAD_POD_STATUSES:
                    logger.warning(
                        "dead pod detected: pod=%s phase=%s reason=%s",
                        pod_id, pod.phase if pod else "NotFound", pod.reason if pod else "",
                    )
                    dead += 1
                    await self._purge_and_notify(pod_id)
                    continue
                await self._health_probe(pod_id, info)
        finally:
            await self.state.unlock(self.state.k.lock_watch(), token)
        logger.debug("watch tick: pods=%d dead=%d duration_ms=%.0f",
                     pods, dead, (time.monotonic() - t0) * 1000)

    async def _health_probe(self, pod_id: str, info: dict[str, str]) -> None:
        """场景 N：K8s Running/Ready 但 SSE hang 死只能靠此探测发现。"""
        pod_ip = info.get("pod_ip", "")
        if not pod_ip:
            self._warn_probe_gap(pod_id, "pod_ip", info.get("scope_id", ""))
            return
        # sse_port 不在 pod:info——从 scope:config 的 pod_spec_json 取
        sse_port = await self._scope_sse_port(info.get("scope_id", ""))
        if not sse_port:
            self._warn_probe_gap(pod_id, "sse_port", info.get("scope_id", ""))
            return
        self._probe_gap_warned.discard(pod_id)
        if await self.k8s.probe_health(pod_ip, sse_port):
            await self.state.reset_health_fail(pod_id)
            return
        fails = await self.state.bump_health_fail(pod_id)
        logger.warning(
            "health probe failed: pod=%s ip=%s consecutive=%d", pod_id, pod_ip, fails,
        )
        if fails >= self.health_fail_threshold:
            logger.warning("half-dead pod judged dead: pod=%s fails=%d", pod_id, fails)
            await self._purge_and_notify(pod_id)

    def _warn_probe_gap(self, pod_id: str, missing: str, scope_id: str) -> None:
        """探测数据缺失（探测被静默跳过的隐患）：按 pod 去重告警一次。"""
        key = f"{pod_id}:{missing}"
        if key in self._probe_gap_warned:
            return
        self._probe_gap_warned.add(key)
        logger.warning(
            "health probe skipped (missing data): pod=%s missing=%s scope=%s "
            "-- 该 Pod 的 SSE 健康探测未执行",
            pod_id, missing, scope_id or "-",
        )

    async def _scope_sse_port(self, scope_id: str) -> int | None:
        if not scope_id:
            return None
        cfg = await self.state.load_scope_config(scope_id)
        try:
            pod_spec = json.loads(cfg.get("pod_spec_json") or "{}")
        except ValueError:
            return None
        return to_int(pod_spec.get("sse_port")) or None

    # -------------------------------------------------------------- reconcile（L）

    async def reconcile_once(self) -> None:
        token = uuid4().hex
        if not await self.state.try_lock(self.state.k.lock_reconcile(), RECONCILE_LOCK_TTL, token):
            return
        t0 = time.monotonic()
        orphans = stale = 0
        view: list[dict[str, str]] = []
        try:
            # 1. Redis↔K8s：Redis 记录的 Pod 在 K8s 已不存在 → 清理（Watch 兜底）
            for pod_id in await self.state.all_pod_ids():
                info = await self.state.pod_info(pod_id)
                if not info:
                    continue
                pod = await self.k8s.get_pod(pod_id, info.get("namespace", "default"))
                if pod is None:
                    logger.warning("orphan pod (absent in k8s): pod=%s", pod_id)
                    orphans += 1
                    await self._purge_and_notify(pod_id)

            # 2. RM↔SM：RM 持有但 SM 已不 route 的 stale Pod → 转 idle（按 pod_ttl 回收）
            for pod_id in await self.state.all_pod_ids():
                scope_id = await self.state.pod_scope(pod_id)
                if scope_id:
                    view.append({"pod_id": pod_id, "scope_id": scope_id})
            if not view or self.sm is None:
                return
            result = await self.sm.reconcile_pods(view)
            now = now_ts()
            for entry in result.get("stale", []):
                await self.state.release(entry["pod_id"], entry["scope_id"], now)
                stale += 1
                logger.info("reconcile stale pod → idle: pod=%s scope=%s",
                            entry["pod_id"], entry["scope_id"])
        finally:
            await self.state.unlock(self.state.k.lock_reconcile(), token)
            logger.debug(
                "reconcile tick: pods=%d orphans=%d stale=%d duration_ms=%.0f",
                len(view), orphans, stale, (time.monotonic() - t0) * 1000,
            )

    # -------------------------------------------------------------- 清理

    async def _purge_and_notify(self, pod_id: str) -> None:
        """三步清理：K8s delete（若还在）→ LUA_PURGE → notify_pod_dead（触发场景 G）。

        幂等：PURGE / delete(NotFound) / notify 都是幂等操作。
        """
        info = await self.state.pod_info(pod_id)
        if info:
            try:
                await self.k8s.delete(pod_id, info.get("namespace", "default"))
            except Exception:  # noqa: BLE001 - 清理尽力而为，PURGE 仍继续
                logger.exception("k8s delete failed during purge: pod=%s", pod_id)
        try:
            scope_id = await self.state.purge(pod_id)
        except Exception:  # noqa: BLE001 - PURGE 失败：watch/reconcile 下拍兜底重试
            logger.exception("redis purge failed: pod=%s", pod_id)
            return
        logger.info("pod purged: pod=%s scope=%s", pod_id, scope_id or "-")
        if self.sm is not None:
            try:
                await self.sm.notify_pod_dead(pod_id)
            except Exception:  # noqa: BLE001 - 30s reconcile 会兜底重试
                logger.exception("notify_pod_dead failed: pod=%s", pod_id)

    # label selector 常量导出（cleanup 默认值，测试引用）
    _POD_LABEL_SELECTOR = POD_LABEL_SELECTOR
