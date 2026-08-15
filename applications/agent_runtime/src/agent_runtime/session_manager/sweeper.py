# coding: utf-8
"""SM 后台 sweeper（SM 设计 §5.4）：到期 pass + 空 Pod pass，每 tick 选主。

- 到期 pass：扫全局 session_expiry，逐个 LUA_EVICT（释放额度 + 唤醒等待者）。
  这是「再也不会被访问的废弃 session」的唯一回收路径。
- 空 Pod pass：扫 pods:registered，SCARD==0 的 Pod 经 LUA_SWEEP_IDLE_NOTIFY
  原子去重 + ZREM 退出候选 → fire-and-forget idle_consider（RM 转 idle 暖池）。

选主：tick 级 SET NX EX 2（多副本同时只有一个扫）；本类只提供 sweep_once，
调度由 main 经 SystemContext.create_single_leader_job 注入。
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from ..util import now_ts
from .state import SessionState

logger = logging.getLogger("agent_runtime.session_manager")

SWEEP_LOCK_TTL = 2          # tick=1s 留余量
IDLE_CONSIDER_RETRY_AFTER = 60  # idle_notified 过期后自动重发（脚本内 EX 60）


class SessionSweeper:
    """SM 老化扫描（无进程内可变状态；每实例一个后台 task，全局 tick 级选主）。"""

    def __init__(self, sm_state: SessionState, rm_facade) -> None:
        self.state = sm_state
        self.rm = rm_facade

    async def sweep_once(self) -> None:
        """一个 tick：抢锁 → 到期 pass → 空 Pod pass。"""
        token = uuid4().hex
        if not await self.state.try_lock(self.state.k.lock_sweep(), SWEEP_LOCK_TTL, token):
            return  # 他副本在本 tick 扫描，跳过
        try:
            await self._expiry_pass()
            await self._empty_pod_pass()
        finally:
            await self.state.unlock(self.state.k.lock_sweep(), token)

    # -------------------------------------------------------------- 到期 pass

    async def _expiry_pass(self) -> None:
        now = now_ts()
        due = await self.state.due_session_ids(now)
        for session_id in due:
            result = await self.state.evict(session_id)
            if result:
                logger.info(
                    "sweep evict expired session: session=%s scope=%s pod=%s remaining=%s",
                    session_id, result["scope_id"], result["pod_id"], result["remaining"],
                )

    # -------------------------------------------------------------- 空 Pod pass

    async def _empty_pod_pass(self) -> None:
        """idle_consider 的唯一触发点。

        统一覆盖三种空 Pod 成因（到期 evict / 惰性 evict / acquire 后从未放置的
        孤儿 Pod）；notified=True 才 fire-and-forget，失败由 60s idle_notified
        过期重发自愈。
        """
        for entry in await self.state.registered_pods():
            scope_id, _, pod_id = entry.partition(":")
            if not pod_id:
                continue  # 键格式异常（scope_id 不含 ':'，防御）
            notified = await self.state.sweep_idle_notify(scope_id, pod_id)
            if notified:
                logger.info("sweep idle_consider: scope=%s pod=%s", scope_id, pod_id)
                self._fire_idle_consider(pod_id, scope_id)

    def _fire_idle_consider(self, pod_id: str, scope_id: str) -> None:
        """不 await：失败不阻塞 sweeper；60s 后 idle_notified 过期可重发。"""

        async def _call() -> None:
            try:
                await self.rm.idle_consider(pod_id=pod_id, scope_id=scope_id)
            except Exception:  # noqa: BLE001 - 自愈路径，记录不中断
                logger.exception(
                    "idle_consider failed (will retry after idle_notified expiry): "
                    "pod=%s scope=%s", pod_id, scope_id,
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(_call(), name=f"idle-consider:{pod_id}")
