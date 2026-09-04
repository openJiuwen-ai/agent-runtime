# coding: utf-8
"""Session Manager route/touch 编排（SM 设计 §5.2 / §5.3）。

route 主循环：幂等回放（handler 层）→ resolve → LUA_ROUTE_PLACE 原子仲裁 →
- refresh/placed：读 pod sse_url 返回 gateway（数据面直连，SM 旁路）；
- scope_full：场景 F 快失败——立即 503 SCOPE_FULL + retry_after，不排队不订阅
  （有界等待 2026-09 已整体拆除：redis-py asyncio RedisCluster 无 pubsub 实现，
  见 docs/feature/2026-09-scope-full-fastfail.md；背压 = gateway 指数退避）；
- need_acquire：调 rm_facade.acquire 扩 +1 Pod → register_pod 登记候选集 → 重跑。

**单次 route 总预算**：need_acquire 分支一轮可触发完整 deploy（ready_timeout
默认 300s），无总预算时单请求可阻塞 max_pods×ready_timeout 且期间持续扩
Pod——主循环以**推导式总预算**（`template.ready_timeout + 余量`）每圈校验，
超则 503（粗化 NoPodAvailable，WARNING 留真因）。冷启动相容性：超预算后 RM
acquire 仍在后台完成并落 idem 缓存（TTL 60s），gateway 同 request_id 重试即
幂等回放结果。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..errors import (
    DEFAULT_RETRY_AFTER,
    DeployFailed,
    InvalidParams,
    MaxPodsReached,
    NoPodAvailable,
    ScopeFull,
)
from ..util import key_unsafe, now_ts
from .config_store import ConfigStore
from .state import SessionState

logger = logging.getLogger("agent_runtime.session_manager")

# 过载参数（SM 设计 §7 默认表；可被 settings 覆盖）
ROUTE_BUDGET_MARGIN_SEC = 10.0        # 总预算推导余量（acquire 收尾/仲裁重跑）


class SessionOrchestrator:
    """route / touch 业务编排（无进程内状态；全部状态在 Redis）。"""

    def __init__(
        self,
        sm_state: SessionState,
        config_store: ConfigStore,
        rm_facade: Any,                      # ResourceManagerFacade（进程内）
        *,
        default_session_ttl: int = 60,
    ) -> None:
        self.state = sm_state
        self.config = config_store
        self.rm = rm_facade
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
        if not (session_id and group_id and bot_id and user_id):
            raise InvalidParams(
                f"route requires session_id/group_id/bot_id/user_id, got "
                f"session_id={session_id!r} group_id={group_id!r} "
                f"bot_id={bot_id!r} user_id={user_id!r}"
            )
        if key_unsafe(session_id):
            # session_id 进键名（session:{sid}）：含 {/} 会破坏 hash tag 同槽性
            raise InvalidParams(
                f"session_id must not contain '{{' or '}}': {session_id!r}"
            )
        # 路由匹配：按 (index, scope_id) 序 first-fit 命中下发 scope（快照求值）
        scope_id, template = await self.config.resolve(user_id, group_id, bot_id)
        # total_deadline（总预算）：扩容 + 重仲裁全链 = ready_timeout + 余量。
        # 封的是 need_acquire 无上界循环（一轮可触发完整 deploy），非冷启动
        # 本身；超预算 503 后 RM acquire 照常完成并落 idem 缓存，同
        # request_id 重试幂等回放（见模块 docstring）。
        total_budget = (
            float(template.ready_timeout or 0) + ROUTE_BUDGET_MARGIN_SEC
        )
        total_deadline = time.monotonic() + total_budget
        t0 = time.monotonic()

        while True:
            # 总预算：need_acquire 一轮可触发完整 deploy（ready_timeout 量级），
            # 无总预算时单请求可阻塞 max_pods×ready_timeout 且持续扩 Pod。
            if time.monotonic() >= total_deadline:
                # 对外粗化 NO_POD_AVAILABLE（acquire 侧惯例，映射前留真因）——
                # 预算只在 need_acquire 路径耗尽，语义即「预算内未扩出 Pod」
                logger.warning(
                    "route: total budget exhausted: scope=%s request_id=%s "
                    "budget=%.0fs (ready_timeout=%ss + margin %.0fs)",
                    scope_id, request_id, total_budget,
                    template.ready_timeout, ROUTE_BUDGET_MARGIN_SEC,
                )
                raise NoPodAvailable(
                    f"scope {scope_id} route budget exhausted "
                    f"({total_budget:.0f}s = ready_timeout "
                    f"{template.ready_timeout}s + margin, acquire included)",
                    retry_after=DEFAULT_RETRY_AFTER,
                )
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
                    # 极端竞态：Pod 刚被 notify_pod_dead 清理。ROUTE_PLACE 的
                    # refresh 分支有 info 存活守卫，下一轮会惰性回收死绑定并
                    # 重新放置（continue 不构成自旋）
                    logger.warning(
                        "route: pod info missing, retrying: scope=%s pod=%s "
                        "session=%s action=%s", scope_id, pod_id, session_id, action,
                    )
                    continue
                logger.info(
                    "route: session=%s scope=%s pod=%s action=%s "
                    "request_id=%s duration_ms=%.1f",
                    session_id, scope_id, pod_id, action,
                    request_id, (time.monotonic() - t0) * 1000,
                )
                return {"pod_sse_url": sse_url, "pod_id": pod_id}

            if action == "scope_full":
                # 场景 F 快失败：Lua 闸门即唯一仲裁，被拒者毫秒级返回。
                # DEBUG 足矣——metrics 中间件每请求已带 error_code=SCOPE_FULL
                # 的 INFO 汇总，过载风暴下此处再 INFO 会双份刷屏
                logger.debug(
                    "route: scope full, fast-fail: scope=%s request=%s "
                    "scope_concurrency=%d",
                    scope_id, request_id, template.scope_concurrency,
                )
                raise ScopeFull(
                    f"scope {scope_id} at concurrency limit "
                    f"({template.scope_concurrency})",
                    retry_after=DEFAULT_RETRY_AFTER,
                )

            # action == "need_acquire"：现有 Pod 全满且未达 max_pods → RM 扩 +1
            pod_id, sse_url = await self._acquire_pod(
                scope_id, template, request_id
            )
            await self.state.register_pod(
                scope_id, pod_id, sse_url, template.deploy_ver()
            )
            # 重跑 ROUTE_PLACE：新 Pod 必被 first-fit 选中

    # -------------------------------------------------------------- 内部

    async def _acquire_pod(
        self, scope_id: str, template: Any, request_id: str
    ) -> tuple[str, str]:
        """调 RM 扩 +1 Pod；RM 异常映射为对外 NO_POD_AVAILABLE(503)。"""
        t0 = time.monotonic()
        try:
            acquired = await self.rm.acquire(
                scope_id=scope_id,
                pod_spec=template.deploy_subset(),
                pool_config=template.pool_config(),
                request_id=request_id,
            )
        except MaxPodsReached as exc:
            # 达 max_pods：总容量已 ≥ scope 预算，只能等额度释放。
            # 对外错误码粗化为 NO_POD_AVAILABLE——映射前留真因，客户端侧不可见
            logger.warning(
                "acquire mapped to NO_POD_AVAILABLE: scope=%s mapped_from=%s "
                "request_id=%s detail=%s",
                scope_id, exc.code, request_id, exc,
            )
            raise NoPodAvailable(
                f"scope {scope_id} reached max_pods={template.max_pods}",
                retry_after=DEFAULT_RETRY_AFTER,
            ) from exc
        except DeployFailed as exc:
            logger.warning(
                "acquire mapped to NO_POD_AVAILABLE: scope=%s mapped_from=%s "
                "request_id=%s detail=%s",
                scope_id, exc.code, request_id, exc,
            )
            raise NoPodAvailable(
                f"deploy failed for scope {scope_id}: {exc}",
                retry_after=DEFAULT_RETRY_AFTER,
            ) from exc
        logger.info(
            "route acquired pod: scope=%s pod=%s duration_ms=%.1f",
            scope_id, acquired["pod_id"], (time.monotonic() - t0) * 1000,
        )
        return acquired["pod_id"], acquired["pod_sse_url"]

    # -------------------------------------------------------------- touch

    async def touch(self, session_id: str) -> bool:
        """保活 / EOS：刷新老化计时；已过期/不存在返回 False（gateway 回退重新 route）。"""
        if not session_id:
            raise InvalidParams("touch requires session_id")
        if key_unsafe(session_id):
            raise InvalidParams(
                f"session_id must not contain '{{' or '}}': {session_id!r}"
            )
        touched, _ = await self.state.touch(
            session_id, now_ts(), self.default_session_ttl
        )
        if touched:
            logger.debug("touch: session=%s touched=%s ttl=%d",
                         session_id, touched, self.default_session_ttl)
        else:
            # 未命中=会话已过期/不存在，gateway 将回退重新 route——生产 INFO 下
            # 必须可见（过期风暴的排障入口）；命中路径保持 DEBUG 防保活刷屏
            logger.info("touch missed: session=%s ttl=%d",
                        session_id, self.default_session_ttl)
        return touched
