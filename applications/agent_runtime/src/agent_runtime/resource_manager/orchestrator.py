# coding: utf-8
"""RM 编排（RM 设计 §5.2 / §5.3）：acquire / idle_consider / update_pool_config / cleanup。

acquire 决策：取暖 Pod 复用（deploy_ver 过滤）→ 无暖 Pod 未达 max_pods 选主
deploy +1 → 达上限 MaxPodsReached。deploy 走 per-scope 锁串行（防并发超配）；
他副本在 deploy 时本请求短暂等待后重跑 ACQUIRE 复用其成果。

红线：错误路径必须清 deploying 占位（防 max_pods 永久虚高）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from uuid import uuid4

from ..errors import DeployFailed, MaxPodsReached
from ..spec_fields import DEPLOY_VER_FIELDS
from ..util import fingerprint, now_ts
from .k8s import DEFAULT_READY_TIMEOUT, K8sPodClient
from .models import PodDeployInfo
from .state import ResourceState

logger = logging.getLogger("agent_runtime.resource_manager")

ACQUIRE_IDEM_TTL = 60          # acquire 结果幂等缓存窗口
DEPLOY_LOCK_TTL = 360          # per-scope deploy 锁（盖住 ready_timeout 300s + 余量）
DEPLOY_WAIT_ON_BUSY = 0.3      # follower 轮询间隔（原输家自旋间隔沿用）
FOLLOWER_WAIT_MARGIN = 10      # follower 等待上界 = ready_timeout + 此余量（注册开销）
NO_CONFIG_LOOP_WARN = 5        # acquire 内 no_config 重跑超过该次数告警（正常 ≤1 次）
FOLLOWER_PROGRESS_LOG_SEC = 5  # follower 轮询进度 INFO 行间隔（限频）


def _deploy_ver(pod_spec: dict[str, Any]) -> str:
    """pod_spec 的 deploy 子集指纹（与 SM Template.deploy_ver() 同一算法/字段，
    两端必须一致——A 类版本过滤依赖它）。kubeconfig 不入指纹（B 类例外）。"""
    return fingerprint({f: pod_spec.get(f) for f in DEPLOY_VER_FIELDS})


class ResourceOrchestrator:
    """RM 业务编排（无进程内可变状态；池状态在 Redis，物理态以 K8s 为准）。"""

    def __init__(self, rm_state: ResourceState, k8s: K8sPodClient) -> None:
        self.state = rm_state
        self.k8s = k8s

    # -------------------------------------------------------------- acquire

    async def acquire(
        self,
        scope_id: str,
        pod_spec: dict[str, Any],
        pool_config: dict[str, Any],
        request_id: str = "",
    ) -> dict[str, str]:
        """SM 现有 Pod 都满时调此扩 +1。返回 {pod_id, pod_sse_url}。

        幂等：同 request_id 重试直接回放缓存结果，不重复 deploy。
        """
        if request_id and (cached := await self._idem_get(request_id)):
            logger.info("acquire idempotent replay: request=%s", request_id)
            return cached

        t0 = time.monotonic()
        no_config_loops = 0
        outcome = ""
        deploy_ver = _deploy_ver(pod_spec)

        # 首见 scope：缓存池参数 + pod_spec（autoscale 补位 deploy 时用）
        if not await self.state.has_scope_config(scope_id):
            await self.state.save_scope_config(
                scope_id,
                {
                    "min_idle_pods": int(pool_config.get("min_idle_pods", 0)),
                    "max_pods": int(pool_config.get("max_pods", 1)),
                    "pod_ttl": int(pool_config.get("pod_ttl", 300)),
                    "pod_concurrency": int(pool_config.get("pod_concurrency", 1)),
                    "deploy_ver": deploy_ver,
                    "pod_spec_json": json.dumps(pod_spec),
                },
            )

        try:
            while True:
                token = uuid4().hex
                action, pod_id, sse_url = await self.state.acquire(
                    scope_id, deploy_ver, token
                )

                if action == "reuse":
                    result = {"pod_id": pod_id, "pod_sse_url": sse_url}
                    outcome = f"reuse pod={pod_id}"
                    if request_id:
                        await self._idem_put(request_id, result)
                    return result

                if action == "max_reached":
                    await self.state.clear_deploy_token(scope_id, token)
                    outcome = "max_reached"
                    raise MaxPodsReached(f"scope {scope_id} reached max_pods")

                if action == "no_config":
                    # 首见建配置后重跑即可（上面已保证写入）
                    no_config_loops += 1
                    if no_config_loops > NO_CONFIG_LOOP_WARN:
                        logger.warning(
                            "acquire no_config loop: scope=%s iterations=%d",
                            scope_id, no_config_loops,
                        )
                    continue

                # action == need_deploy：选主串行 deploy
                lock_key = self.state.k.lock_deploy(scope_id)
                lock_token = f"deploy-{token}"
                if not await self.state.try_lock(lock_key, DEPLOY_LOCK_TTL, lock_token):
                    # 输家：清占位 → 进 follower 等待室（上限 pc-1，overflow 严格
                    # 快失败），等 leader 的 Pod 注册后**直接复用**——RM 全程不读
                    # SM 的容量键（闸门归属不变），也修掉「输家自建第 2 个空 Pod」
                    # 的跨副本冷竞争浪费（见 handoff §十一.1 开放问题）
                    await self.state.clear_deploy_token(scope_id, token)
                    result = await self._follow_leader(
                        scope_id, pod_spec, pool_config, request_id)
                    outcome = f"follower_reuse pod={result.get('pod_id', '?')}"
                    if request_id:
                        await self._idem_put(request_id, result)
                    return result
                try:
                    pod_id, sse_url = await self._deploy_and_register(
                        scope_id, pod_spec, deploy_ver, token, idle_flag=False
                    )
                finally:
                    await self.state.unlock(lock_key, lock_token)

                result = {"pod_id": pod_id, "pod_sse_url": sse_url}
                outcome = f"deployed pod={pod_id}"
                if request_id:
                    await self._idem_put(request_id, result)
                return result
        finally:
            # 每次_acquire 一行结果（含失败路径；真因异常链由 handler/_follow_leader 留痕）
            logger.info(
                "acquire done: scope=%s request=%s outcome=%s duration_ms=%.1f",
                scope_id, request_id or "-", outcome or "error",
                (time.monotonic() - t0) * 1000,
            )

    async def _follow_leader(
        self,
        scope_id: str,
        pod_spec: dict[str, Any],
        pool_config: dict[str, Any],
        request_id: str,
    ) -> dict[str, str]:
        """deploy 锁输家的 follower 等待室：等 leader 的 Pod 注册后直接复用。

        设计定案（M8，讨论见 e2e-test-cases §8.2 / handoff §十一.1）：
        - 准入 = 原子闸门（ZSET+deadline），上限 ``pod_concurrency - 1``——
          leader 会话之外新 Pod 恰剩这些槽；**overflow 严格快失败**；
        - 等待有界（ready_timeout + 余量）；leader 失败（锁空闲且无进展）
          → follower **不接管**直接失败（同镜像同环境大概率也失败）；
        - 检测到新 Pod 注册（进展）→ 返回该 Pod，与 reuse 分支同构——
          SM 侧重跑仲裁即可，RM 全程不读 SM 容量键（红线不破）。
        """
        pc = max(int(pool_config.get("pod_concurrency", 1)), 1)
        max_followers = pc - 1
        ready_timeout = int(pod_spec.get("ready_timeout") or DEFAULT_READY_TIMEOUT)
        now = now_ts()
        admitted = await self.state.try_add_deploy_follower(
            scope_id, request_id, max_followers,
            now + ready_timeout + FOLLOWER_WAIT_MARGIN, now,
        )
        if not admitted:
            logger.warning(
                "follower waiting room full: scope=%s follower=%s max_followers=%d",
                scope_id, request_id, max_followers,
            )
            raise MaxPodsReached(
                f"scope {scope_id} deploy followers full ({max_followers}); "
                f"retry after leader registers")

        pods_before = set(await self.state.pod_ids(scope_id))
        deadline = time.monotonic() + ready_timeout + FOLLOWER_WAIT_MARGIN
        lock_key = self.state.k.lock_deploy(scope_id)
        logger.info(
            "follower waiting for leader deploy: scope=%s follower=%s "
            "max_followers=%d ready_timeout=%s wait_cap_s=%d",
            scope_id, request_id, max_followers, ready_timeout,
            ready_timeout + FOLLOWER_WAIT_MARGIN,
        )
        waited_log_at = started = time.monotonic()
        try:
            while True:
                await asyncio.sleep(DEPLOY_WAIT_ON_BUSY)
                now_mono = time.monotonic()
                if now_mono - waited_log_at >= FOLLOWER_PROGRESS_LOG_SEC:
                    # 进度行 INFO：ready_timeout 最长 300s，INFO 下这段等待
                    # 不能是日志空白（部署风暴期正是最需要观测的窗口）
                    logger.info(
                        "follower still waiting: scope=%s follower=%s waited_s=%.0f",
                        scope_id, request_id, now_mono - started,
                    )
                    waited_log_at = now_mono
                new_pods = [p for p in await self.state.pod_ids(scope_id)
                            if p not in pods_before]
                if new_pods:
                    for pod in new_pods:
                        info = await self.state.pod_info(pod)
                        if info.get("pod_sse_url"):
                            logger.info(
                                "acquire follower reuses leader pod: scope=%s "
                                "pod=%s follower=%s", scope_id, pod, request_id)
                            return {"pod_id": pod,
                                    "pod_sse_url": info["pod_sse_url"]}
                    # 新 Pod 已入池但 info 尚未可见（原子脚本内不存在此窗口，
                    # 防御性兜底）：视为有进展，跳过失败判定等下一轮
                    continue
                if not await self.state.lock_held(lock_key):
                    logger.warning(
                        "follower aborts, leader deploy aborted: scope=%s "
                        "follower=%s waited_s=%.1f",
                        scope_id, request_id, now_mono - started,
                    )
                    raise DeployFailed(
                        f"scope {scope_id} leader deploy aborted; follower aborts")
                if now_mono >= deadline:
                    logger.warning(
                        "follower wait timeout: scope=%s follower=%s "
                        "waited_s=%d ready_timeout=%s",
                        scope_id, request_id,
                        ready_timeout + FOLLOWER_WAIT_MARGIN, ready_timeout,
                    )
                    raise MaxPodsReached(
                        f"scope {scope_id} follower wait timeout "
                        f"({ready_timeout + FOLLOWER_WAIT_MARGIN}s)")
        finally:
            # 错误路径双清纪律：follower 成员必须退出（防虚占 pc-1 名额）；
            # 崩溃遗留由闸门的 ZREMRANGEBYSCORE(deadline) 兜底
            await self.state.remove_deploy_follower(scope_id, request_id)

    async def _deploy_and_register(
        self,
        scope_id: str,
        pod_spec: dict[str, Any],
        deploy_ver: str,
        deploy_token: str,
        *,
        idle_flag: bool,
    ) -> tuple[str, str]:
        """create + wait Ready + REGISTER。任一步失败：清占位 + 清物理 Pod 后上抛。

        红线含 CancelledError（优雅停机会取消在飞的 autoscale/route tick）：
        except Exception 接不住 BaseException，占位泄漏会把池永久堵死
        （真环境 2026-08-26 实测：两次停机各泄一个 warm 占位 → max_pods 虚满）。
        REGISTER 同样在保护内——注册步失败（Redis 抖动/取消）不清占位一样虚占
        max_pods。物理清理：k8s.deploy 失败/取消可能在集群里留下已建 Pod
        （DeployFailed 契约携带 pod_id/namespace），此处兜底删除防孤儿；
        **register 步失败时异常不带 pod_id 属性——用已到手的 info 兜底删除**
        （物理 Pod 已建 Ready，不删就成 pods:all 之外的孤儿：watch/reconcile
        只做 Redis→K8s 单向对账，无人认领、无上界累积）。
        """
        t0 = time.monotonic()
        info: PodDeployInfo | None = None
        try:
            info = await self.k8s.deploy(pod_spec)
            sse_url = (
                f"http://{info.pod_ip}:{pod_spec.get('sse_port', 8080)}"
                f"{pod_spec.get('sse_path', '/sse')}"
            )
            await self.state.register_pod(
                pod_id=info.pod_id,
                scope_id=scope_id,
                pod_sse_url=sse_url,
                pod_ip=info.pod_ip,
                namespace=info.namespace,
                deploy_ver=deploy_ver,
                deploy_token=deploy_token,
                idle_flag=idle_flag,
                now=now_ts(),
                sse_port=int(pod_spec.get("sse_port") or 8080),
                health_path=str(pod_spec.get("health_path") or "/health"),
            )
        except BaseException as exc:   # noqa: BLE001 - 占位清理红线含取消路径
            try:
                await self.state.clear_deploy_token(scope_id, deploy_token)
            except Exception:  # noqa: BLE001 - 清理失败不掩盖原始异常
                logger.exception(
                    "clear deploying token failed during aborted deploy: "
                    "scope=%s token=%s", scope_id, deploy_token,
                )
            orphan = getattr(exc, "pod_id", "") or (info.pod_id if info else "")
            orphan_ns = (getattr(exc, "namespace", "")
                         or (info.namespace if info else "") or "default")
            if orphan:
                try:
                    await self.k8s.delete(orphan, orphan_ns)
                except Exception:  # noqa: BLE001 - 尽力而为，孤儿交由运维 cleanup
                    logger.exception(
                        "orphan pod cleanup failed: pod=%s", orphan,
                    )
            if isinstance(exc, DeployFailed):
                logger.warning(
                    "deploy failed: scope=%s duration_ms=%.1f detail=%s",
                    scope_id, (time.monotonic() - t0) * 1000, exc,
                )
                raise
            if isinstance(exc, asyncio.CancelledError):
                logger.warning(
                    "deploy cancelled (shutdown?), token cleared: scope=%s "
                    "duration_ms=%.1f", scope_id, (time.monotonic() - t0) * 1000,
                )
                raise
            logger.exception(
                "deploy error (mapped to DeployFailed): scope=%s duration_ms=%.1f",
                scope_id, (time.monotonic() - t0) * 1000,
            )
            raise DeployFailed(f"deploy error for scope {scope_id}: {exc}") from exc
        logger.info(
            "deployed pod: scope=%s pod=%s ip=%s namespace=%s idle_flag=%s url=%s "
            "duration_ms=%.1f",
            scope_id, info.pod_id, info.pod_ip, info.namespace, idle_flag, sse_url,
            (time.monotonic() - t0) * 1000,
        )
        return info.pod_id, sse_url

    # -------------------------------------------------------------- idle_consider

    async def idle_consider(self, pod_id: str, scope_id: str) -> dict[str, bool]:
        """该 scope 在该 Pod 上已无会话 → 转 idle 暖池（幂等）。"""
        transitioned = await self.state.release(pod_id, scope_id, now_ts())
        if transitioned:
            await self.state.redis.hset(self.state.k.pod_info(pod_id), "phase", "idle")
        logger.info("idle_consider: scope=%s pod=%s transitioned=%s",
                    scope_id, pod_id, transitioned)
        return {"transitioned_to_idle": transitioned}

    # -------------------------------------------------------------- update_pool_config

    async def update_pool_config(
        self,
        scope_id: str,
        pool_config: dict[str, Any],
        pod_spec: dict[str, Any] | None = None,
    ) -> dict[str, bool]:
        """config_sync 主动刷新（场景 M）：HSET 覆盖（幂等），立即生效。

        A 类变更附带 pod_spec：同时刷新 deploy_ver / pod_spec_json →
        autoscale 补位的新暖 Pod 用新 deploy 字段。mapping 永不含 generation
        ——代次只经 bump_generation 单调递增，config_sync 推送不重置。
        """
        mapping: dict[str, Any] = {
            "min_idle_pods": int(pool_config.get("min_idle_pods", 0)),
            "max_pods": int(pool_config.get("max_pods", 1)),
            "pod_ttl": int(pool_config.get("pod_ttl", 300)),
        }
        if pod_spec is not None:
            mapping["deploy_ver"] = _deploy_ver(pod_spec)
            mapping["pod_spec_json"] = json.dumps(pod_spec)
        await self.state.save_scope_config(scope_id, mapping)
        logger.info("update_pool_config: scope=%s fields=%s", scope_id, sorted(mapping))
        return {"updated": True}

    async def bump_generation(self, scope_id: str) -> int:
        """config_refresh 的代次日落（facade 出口）：scope 代次 +1，返回新代次。

        效果：现有 Pod 的 generation 全部落后于 config → LUA_ACQUIRE 不再复用、
        _current_version_idle 判 stale（reclaim 按 pod_ttl 回收、autoscale 重建）。
        """
        generation = await self.state.bump_generation(scope_id)
        logger.info("bump_generation: scope=%s generation=%d", scope_id, generation)
        return generation

    # -------------------------------------------------------------- cleanup

    async def cleanup(self, namespace: str | None, label_selector: str | None) -> int:
        """运维批删（灾难恢复 / 重部署 / 清孤儿）。不操作 Redis 编排态；
        被删 Pod 由 watch/reconcile 兜底发现（NotFound → PURGE + notify_pod_dead），
        清完后 autoscale 重建 min_idle_pods。"""
        from .models import POD_LABEL_SELECTOR

        ns = namespace or self.k8s.default_namespace
        selector = label_selector or POD_LABEL_SELECTOR
        try:
            pods = await self.k8s.list_pods(ns, selector)
        except Exception as exc:  # noqa: BLE001
            # namespace 不存在（404）→ 无可清资源，返回 0（与 cluster 级
            # 凭据下「list 不存在 ns 得空列表」的行为对齐）；
            # 403（RBAC 越权）等仍快速失败——静默清零会掩盖部署配错。
            # 用 getattr(exc, "status") 判定，避免 local 模式引入 kubernetes_asyncio 依赖。
            if getattr(exc, "status", None) == 404:
                pods = []
            else:
                raise
        cleaned = 0
        for info in pods:
            await self.k8s.delete(info.pod_id, ns)
            cleaned += 1
            # 逐 Pod 留痕：批删中途中断（单个 delete 失败上抛）时能看到删到哪；
            # 低频运维操作，无刷屏风险（k8s.delete 自身明细在 DEBUG）
            logger.info("cleanup deleted pod: pod=%s namespace=%s", info.pod_id, ns)
        logger.warning("cleanup: namespace=%s selector=%s cleaned=%d", ns, selector, cleaned)
        return cleaned

    # -------------------------------------------------------------- 幂等缓存

    async def known_scope_ids(self) -> list[str]:
        """RM 已知 scope 枚举（facade 出口；config_sync drain 收敛用）。"""
        return await self.state.known_scope_ids()

    async def _idem_get(self, request_id: str) -> dict[str, str] | None:
        key = f"{self.state.prefix}idem:{request_id}"
        raw = await self.state.redis.get(key)
        if not raw:
            return None
        text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        try:
            value = json.loads(text)
        except ValueError:
            logger.warning(
                "idem cache corrupt, ignoring: request=%s raw_prefix=%r",
                request_id, text[:80],
            )
            return None
        # 存活校验：缓存的 Pod 可能已被 watch/reclaim 判死 PURGE——回放死 Pod
        # 会在 SM 侧复活注册并持续喂死地址给重试客户端。判死则弃缓存走全新
        # acquire（成功后覆盖写回）。
        pod_id = value.get("pod_id", "")
        if pod_id and not await self.state.pod_info(pod_id):
            logger.warning(
                "acquire idem cache stale (pod purged), ignoring: request=%s pod=%s",
                request_id, pod_id,
            )
            await self.state.redis.delete(key)
            return None
        # 命中即续期（重试窗口内反复回放）
        await self.state.redis.expire(key, ACQUIRE_IDEM_TTL)
        return value

    async def _idem_put(self, request_id: str, result: dict[str, str]) -> None:
        await self.state.redis.set(
            f"{self.state.prefix}idem:{request_id}",
            json.dumps(result), ex=ACQUIRE_IDEM_TTL,
        )


def monotonic_now() -> float:  # 供 sweeper 测试注入时间
    return time.monotonic()
