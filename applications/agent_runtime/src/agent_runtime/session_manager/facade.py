# coding: utf-8
"""SessionManagerFacade：RM → SM 的进程内调用入口（SM 设计 §2.3 / §2.5）。

- notify_pod_dead(pod_id)：Pod 死亡 / reclaim 两种场景都经此清洗（场景 G）——
  逐 session evict（释放额度）→ 清该 Pod 全部 SM 注册；幂等。
- reconcile_pods(view)：孤儿对账（场景 L）——SM 对每个 (pod, scope) 查
  scope:pods 成员资格，非成员 = SM 已不用 → stale；只读、幂等、单向。
"""

from __future__ import annotations

import logging
from typing import Any

from .state import SessionState

logger = logging.getLogger("agent_runtime.session_manager")


class SessionManagerFacade:
    """供 resource_manager 模块进程内调用的 SM 能力。"""

    def __init__(self, sm_state: SessionState) -> None:
        self.state = sm_state

    async def notify_pod_dead(self, pod_id: str) -> dict[str, list[str]]:
        """清洗死 Pod 上的全部会话与注册。返回 {invalidated: [session_id,...]}。

        幂等：重复调用时 scopes 反查为空 → 无副作用。
        """
        invalidated: list[str] = []
        for scope_id in await self.state.pod_scopes(pod_id):
            for session_id in await self.state.pod_session_ids(scope_id, pod_id):
                await self.state.evict(session_id)   # 原子：四处同删
                invalidated.append(session_id)
            await self.state.cleanup_pod(scope_id, pod_id)
        if invalidated:
            logger.warning(
                "notify_pod_dead: pod=%s invalidated_sessions=%s", pod_id, invalidated
            )
        else:
            logger.info("notify_pod_dead: pod=%s no affected sessions", pod_id)
        return {"invalidated": invalidated}

    async def reconcile_pods(
        self, view: list[dict[str, str]]
    ) -> dict[str, list[dict[str, str]]]:
        """孤儿对账：RM 持有的 (pod, scope) 中，SM 已不再 route 的标为 stale。

        stale 判定 = ``scope:{scope_id}:pods`` 非成员（SM 已 idle_consider /
        notify_pod_dead ZREM），不误杀仍有活跃会话的 Pod。
        """
        stale: list[dict[str, str]] = []
        for entry in view:
            pod_id = entry.get("pod_id", "")
            scope_id = entry.get("scope_id", "")
            if not (pod_id and scope_id):
                continue
            score = await self.state.redis.zscore(
                self.state.k.scope_pods(scope_id), pod_id
            )
            if score is None:
                stale.append({"pod_id": pod_id, "scope_id": scope_id})
        if stale:
            logger.info("reconcile_pods: stale=%s", stale)
        return {"stale": stale}
