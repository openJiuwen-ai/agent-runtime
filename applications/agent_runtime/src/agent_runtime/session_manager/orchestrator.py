# coding: utf-8
"""Session Manager route/touch 编排（SM 设计 §5.2 / §5.3）。

route 主循环：幂等回放（handler 层）→ resolve → LUA_ROUTE_PLACE 原子仲裁 →
- refresh/placed：读 pod sse_url 返回 gateway（数据面直连，SM 旁路）；
- scope_full：有界等待队列（场景 F）——队列满快失败 503，队列内等 free 信号，
  超 scope_full_timeout → 504；
- need_acquire：调 rm_facade.acquire 扩 +1 Pod → register_pod 登记候选集 → 重跑。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..errors import (
    DeployFailed,
    InvalidParams,
    MaxPodsReached,
    NoPodAvailable,
    ScopeFullTimeout,
    ScopeQueueFull,
)
from ..util import now_ts, scope_id_of
from .config_store import ConfigStore
from .state import SessionState

logger = logging.getLogger("agent_runtime.session_manager")

# 过载参数（SM 设计 §7 默认表；可被 settings 覆盖）
DEFAULT_SCOPE_FULL_TIMEOUT = 30.0     # scope 满（队列内）阻塞上限
DEFAULT_RETRY_AFTER = 1               # 过载响应建议重试间隔（秒）


class SessionOrchestrator:
    """route / touch 业务编排（无进程内状态；全部状态在 Redis）。"""

    def __init__(
        self,
        sm_state: SessionState,
        config_store: ConfigStore,
        rm_facade: Any,                      # ResourceManagerFacade（进程内）
        *,
        scope_full_timeout: float = DEFAULT_SCOPE_FULL_TIMEOUT,
        default_session_ttl: int = 60,
    ) -> None:
        self.state = sm_state
        self.config = config_store
        self.rm = rm_facade
        self.scope_full_timeout = scope_full_timeout
        self.default_session_ttl = default_session_ttl

    # -------------------------------------------------------------- route

    async def route(
        self,
        request_id: str,
        session_id: str,
        group_id: str,
        bot_id: str,
        user_id: str | None = None,
    ) -> dict[str, str]:
        """同步路由 + 占额度（关键路径）。返回 {pod_sse_url, pod_id}。"""
        if not (session_id and group_id and bot_id):
            raise InvalidParams(
                f"route requires session_id/group_id/bot_id, got session_id={session_id!r} "
                f"group_id={group_id!r} bot_id={bot_id!r}"
            )
        scope_id = scope_id_of(group_id, bot_id)

        template = await self.config.resolve(scope_id, group_id, bot_id)
        deadline = time.monotonic() + self.scope_full_timeout
        waitering = False  # 是否已进等待队列（异常路径要出队）

        try:
            while True:
                now = now_ts()
                action, pod_id = await self.state.route_place(
                    session_id=session_id,
                    scope_id=scope_id,
                    expiry_ts=now + template.session_ttl,
                    session_ttl=template.session_ttl,
                    scope_concurrency=template.scope_concurrency,
                    pod_concurrency=template.pod_concurrency,
                    max_pods=template.max_pods,
                    now=now,
                )

                if action in ("refresh", "placed"):
                    sse_url = await self.state.pod_sse_url(scope_id, pod_id)
                    if not sse_url:
                        # 极端竞态：Pod 刚被 notify_pod_dead 清理——重跑即可
                        logger.warning(
                            "route: pod info missing, retrying: scope=%s pod=%s "
                            "session=%s action=%s", scope_id, pod_id, session_id, action,
                        )
                        continue
                    logger.info(
                        "route: session=%s scope=%s pod=%s action=%s",
                        session_id, scope_id, pod_id, action,
                    )
                    return {"pod_sse_url": sse_url, "pod_id": pod_id}

                if action == "scope_full":
                    await self._wait_for_capacity(
                        scope_id, request_id, deadline, template
                    )
                    waitering = False
                    continue  # 被唤醒后重跑 Lua；原子 admit 是唯一仲裁，败者重 wait

                # action == "need_acquire"：现有 Pod 全满且未达 max_pods → RM 扩 +1
                pod_id, sse_url = await self._acquire_pod(
                    scope_id, template, request_id
                )
                await self.state.register_pod(
                    scope_id, pod_id, sse_url, template.deploy_ver()
                )
                # 重跑 ROUTE_PLACE：新 Pod 必被 first-fit 选中
        finally:
            if waitering:
                await self.state.remove_waiter(scope_id, request_id)

    # -------------------------------------------------------------- 内部

    async def _acquire_pod(
        self, scope_id: str, template: Any, request_id: str
    ) -> tuple[str, str]:
        """调 RM 扩 +1 Pod；RM 异常映射为对外 NO_POD_AVAILABLE(503)。"""
        try:
            acquired = await self.rm.acquire(
                scope_id=scope_id,
                pod_spec=template.deploy_subset(),
                pool_config=template.pool_config(),
                request_id=request_id,
            )
        except MaxPodsReached as exc:
            # 达 max_pods：总容量已 ≥ scope 预算，只能等额度释放
            raise NoPodAvailable(
                f"scope {scope_id} reached max_pods={template.max_pods}",
                retry_after=DEFAULT_RETRY_AFTER,
            ) from exc
        except DeployFailed as exc:
            raise NoPodAvailable(
                f"deploy failed for scope {scope_id}: {exc}",
                retry_after=DEFAULT_RETRY_AFTER,
            ) from exc
        logger.info(
            "route acquired pod: scope=%s pod=%s", scope_id, acquired["pod_id"]
        )
        return acquired["pod_id"], acquired["pod_sse_url"]

    async def _wait_for_capacity(
        self, scope_id: str, request_id: str, deadline: float, template: Any
    ) -> None:
        """场景 F：有界等待。队列满 → 503 快失败；超 deadline → 504；否则等 free 信号。

        订阅 + ≤500ms 安全轮询双保险（兜「publish 早于 subscribe」的丢失）。
        """
        max_waiters = 2 * max(template.scope_concurrency, 1)
        if time.monotonic() >= deadline:
            raise ScopeFullTimeout(
                f"scope {scope_id} full: waited over {self.scope_full_timeout}s",
                retry_after=DEFAULT_RETRY_AFTER,
            )
        # 原子入队（SADD 先行 + 超限自退）：并发同时到达也不会超收
        if not await self.state.try_add_waiter(scope_id, request_id, max_waiters):
            waiters = await self.state.waiter_count(scope_id)
            raise ScopeQueueFull(
                f"scope {scope_id} waiter queue full ({waiters}/{max_waiters})",
                retry_after=DEFAULT_RETRY_AFTER,
            )
        logger.info("route: scope_full, waiting: scope=%s request=%s", scope_id, request_id)
        channel = self.state.k.scope_free_channel(scope_id)
        pubsub = self.state.redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ScopeFullTimeout(
                        f"scope {scope_id} full: waited over {self.scope_full_timeout}s",
                        retry_after=DEFAULT_RETRY_AFTER,
                    )
                message = await pubsub.get_message(
                    timeout=min(0.5, remaining)
                )
                if message and message.get("type") == "message":
                    return  # 有人释放额度，重跑 Lua 仲裁
        finally:
            await self.state.remove_waiter(scope_id, request_id)
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    # -------------------------------------------------------------- touch

    async def touch(self, session_id: str) -> bool:
        """保活 / EOS：刷新老化计时；已过期/不存在返回 False（gateway 回退重新 route）。"""
        if not session_id:
            raise InvalidParams("touch requires session_id")
        touched, _ = await self.state.touch(
            session_id, now_ts(), self.default_session_ttl
        )
        return touched
