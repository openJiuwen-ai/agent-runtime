# coding: utf-8
"""agent-runtime 服务组装（唯一可运行壳的核心）。

一个进程、一个 App（prefix /api/session）、两个模块：
- session_manager（持 App，注册 4 个 HTTP handler）
- resource_manager（无 App/端口/prefix，纯进程内 Facade + 后台任务）

两个 SystemContext 指向**同一 Redis + 同一 DB**，仅 key_prefix 不同：
- sm_sysctx（session_manager）—— OrchestratorSystemContext 子类，其
  start()/stop() 一并拉起/停止 rm_sysctx、两 Facade 互绑与全部后台任务
  （后台任务生命周期经 SystemContext 注入，不改 App）。
- rm_sysctx（resource_manager）

App 的 lifespan 只认一个 ctx_factory——返回 sm_sysctx，其余生命周期由其
子类 start()/stop() 级联（开发交接文档 §七「框架/环境」约定）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from openjiuwen_runtime.service import App, SystemContext
from openjiuwen_runtime.service.config import ServiceConfig

from . import errors as app_errors
from .config import RM_KEY_PREFIX, SERVICE_PREFIX, SM_KEY_PREFIX, AgentRuntimeConfig
from .debug_api import register_debug_api
from .metrics import MetricsRegistry, request_metrics_middleware
from .resource_manager.facade import ResourceManagerFacade
from .resource_manager.k8s import FakeK8sPodClient, RealK8sPodClient
from .resource_manager.orchestrator import ResourceOrchestrator
from .resource_manager.state import ResourceState
from .resource_manager.sweeper import ResourceSweeper
from .session_manager.config_store import (
    ROUTING_RULE_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    ConfigStore,
)
from .session_manager.facade import SessionManagerFacade
from .session_manager.handlers import register_handlers
from .session_manager.orchestrator import SessionOrchestrator
from .session_manager.state import SessionState
from .session_manager.sweeper import SessionSweeper

logger = logging.getLogger("agent_runtime")

# 业务错误码 → HTTP 状态注册（幂等）
app_errors.register_codes()

# 各后台 job 单次 tick 上限（防 redis/k8s IO 抖动挂死 _run_forever 循环；
# 正常 tick 远低于这些值，超时取消本拍记日志、下一拍重试）：
# - sm_sweep：到期 evict（≤1000/拍）+ 空 Pod 扫描，纯 redis 快操作；
# - rm_autoscale：最重路径为 deploy 等 Ready（ready_timeout 默认 300s，
#   对齐 DEPLOY_LOCK_TTL=360 再留余量）；
# - rm_reclaim / rm_watch / rm_reconcile：逐 Pod 的 k8s delete/get_pod/健康
#   探测（httpx 3s/次），按 Pod 规模给足宽限。
TICK_TIMEOUTS = {
    "sm_sweep": 30,
    "rm_autoscale": 370,
    "rm_reclaim": 60,
    "rm_watch": 300,
    "rm_reconcile": 300,
}


def build_resources(
    settings: ServiceConfig, arc: AgentRuntimeConfig
) -> tuple[Any, Any, Any]:
    """构造共享物理资源（redis client / db handler / k8s client）。

    server 模式：真 Redis（from_url）+ MySQL + kubernetes_asyncio；
    local 模式：进程内 fakeredis + SQLite + FakeK8s（仅供开发调试）。
    """
    if arc.mode == "local":
        import os

        from fakeredis.aioredis import FakeRedis
        from openjiuwen_runtime.foundation.db import SQLiteHandler

        redis_client = FakeRedis()
        # 文件型 SQLite（:memory: 在连接池下会丢表；local 模式仅供开发调试）
        db = SQLiteHandler(os.getenv("AGENT_RUNTIME_SQLITE_PATH", "./agent_runtime_local.db"))
        k8s = FakeK8sPodClient(default_namespace=arc.default_namespace)
        return redis_client, db, k8s

    from openjiuwen_runtime.service.bootstrap import build_db_handler, build_redis_client

    redis_client = build_redis_client(settings)
    db = build_db_handler(settings)
    if db is None:
        raise RuntimeError(
            "agent-runtime server 模式必须配置 DB（OPENJIUWEN_SERVICE_DB_TYPE=mysql）"
        )
    k8s = RealK8sPodClient(
        kubeconfig=arc.kubeconfig, default_namespace=arc.default_namespace
    )
    return redis_client, db, k8s


class OrchestratorSystemContext(SystemContext):
    """SM 侧 SystemContext：级联管理 RM ctx + Facade + 后台任务的生命周期。"""

    def __init__(
        self,
        *,
        redis_client: Any,
        db: Any,
        k8s: Any,
        settings: ServiceConfig,
        arc: AgentRuntimeConfig,
        instance_id: str | None = None,
        owns_resources: bool = True,
    ) -> None:
        super().__init__(
            redis=redis_client,
            db=db,
            settings=settings,
            key_prefix=SM_KEY_PREFIX,
            table_definitions=[SERVICE_CONFIG_TEMPLATE_TABLE_DEF, ROUTING_RULE_TABLE_DEF],
            instance_id=instance_id,
            _owns_db=owns_resources,
            _owns_redis=owns_resources,
        )
        # RM ctx：共享 redis/db（不重复持有），仅前缀不同
        self.rm_sysctx = SystemContext(
            redis=redis_client,
            db=db,
            settings=settings,
            key_prefix=RM_KEY_PREFIX,
            instance_id=self.instance_id,
            logger=self.logger,
        )
        self.arc = arc
        self.k8s = k8s
        self._jobs: list[Any] = []
        self._bind_modules()

    # -------------------------------------------------------------- 组装

    def _bind_modules(self) -> None:
        """两模块互绑（先构造后绑定破解循环引用）。"""
        sm_state = SessionState(self.redis)
        rm_state = ResourceState(self.redis)

        self.sm_facade = SessionManagerFacade(sm_state)

        rm_orchestrator = ResourceOrchestrator(rm_state, self.k8s)
        self.rm_facade = ResourceManagerFacade(rm_orchestrator)

        self.sm_config_store = ConfigStore(
            self.db, sm_state,
            push_pool_config=self.rm_facade.update_pool_config,
        )
        self.sm_orchestrator = SessionOrchestrator(
            sm_state,
            self.sm_config_store,
            self.rm_facade,
            scope_full_timeout=self.arc.scope_full_timeout,
            default_session_ttl=self.arc.default_session_ttl,
        )
        self.sm_sweeper = SessionSweeper(sm_state, self.rm_facade)
        self.rm_sweeper = ResourceSweeper(
            rm_state, self.k8s, self.sm_facade, orchestrator=rm_orchestrator,
        )

    def _build_jobs(self) -> list[Any]:
        """全部后台任务（tick 级选主锁；多副本全局单副本执行写操作）。"""
        arc = self.arc
        jobs = [
            self.create_single_leader_job(
                name="sm_sweep", on_tick=self.sm_sweeper.sweep_once,
                interval_sec=arc.sweep_interval, lock_key="agent_runtime:job:sm_sweep",
                tick_timeout_sec=TICK_TIMEOUTS["sm_sweep"],
            ),
            self.rm_sysctx.create_single_leader_job(
                name="rm_autoscale", on_tick=self.rm_sweeper.autoscale_once,
                interval_sec=arc.autoscale_interval,
                lock_key="agent_runtime:job:rm_autoscale",
                tick_timeout_sec=TICK_TIMEOUTS["rm_autoscale"],
            ),
            self.rm_sysctx.create_single_leader_job(
                name="rm_reclaim", on_tick=self.rm_sweeper.reclaim_once,
                interval_sec=arc.reclaim_interval,
                lock_key="agent_runtime:job:rm_reclaim",
                tick_timeout_sec=TICK_TIMEOUTS["rm_reclaim"],
            ),
            self.rm_sysctx.create_single_leader_job(
                name="rm_watch", on_tick=self.rm_sweeper.watch_once,
                interval_sec=arc.watch_interval,
                lock_key="agent_runtime:job:rm_watch",
                tick_timeout_sec=TICK_TIMEOUTS["rm_watch"],
            ),
            self.rm_sysctx.create_single_leader_job(
                name="rm_reconcile", on_tick=self.rm_sweeper.reconcile_once,
                interval_sec=arc.reconcile_interval,
                lock_key="agent_runtime:job:rm_reconcile",
                tick_timeout_sec=TICK_TIMEOUTS["rm_reconcile"],
            ),
        ]
        return jobs

    # -------------------------------------------------------------- 生命周期

    async def start(self) -> None:
        await super().start()
        await self.rm_sysctx.start()
        try:
            await self.k8s.start()
        except Exception:  # noqa: BLE001 - k8s 不可用只影响扩缩容，不阻断启动
            self.logger.exception("kubernetes client start failed (scale in/out degraded)")
        self._jobs = self._build_jobs()
        interval_by_name = self._job_intervals()
        for job in self._jobs:
            await job.start()
            self.logger.info(
                "background job registered: name=%s interval_sec=%s tick_timeout_sec=%s",
                job.name, interval_by_name.get(job.name, "?"),
                TICK_TIMEOUTS.get(job.name),
            )
        self.logger.info(
            "config summary: mode=%s namespace=%s sweep=%ss autoscale=%ss "
            "reclaim=%ss watch=%ss reconcile=%ss scope_full_timeout=%ss "
            "default_session_ttl=%ss kubeconfig=%s",
            self.arc.mode, self.arc.default_namespace,
            self.arc.sweep_interval, self.arc.autoscale_interval,
            self.arc.reclaim_interval, self.arc.watch_interval,
            self.arc.reconcile_interval, self.arc.scope_full_timeout,
            self.arc.default_session_ttl,
            "set" if self.arc.kubeconfig else "in-cluster",
        )
        self.logger.info(
            "agent-runtime started: instance=%s mode=%s port=%s",
            self.instance_id, self.arc.mode,
            getattr(self.settings, "port", "?"),
        )

    def _job_intervals(self) -> dict[str, int]:
        """诊断用：job 名 → 调度间隔（arc）。"""
        return {
            "sm_sweep": self.arc.sweep_interval,
            "rm_autoscale": self.arc.autoscale_interval,
            "rm_reclaim": self.arc.reclaim_interval,
            "rm_watch": self.arc.watch_interval,
            "rm_reconcile": self.arc.reconcile_interval,
        }

    async def jobs_snapshot(self) -> list[dict[str, Any]]:
        """诊断用：5 个后台任务的间隔/超时/计数器/当前 leader（/debug/overview）。"""
        intervals = self._job_intervals()
        out: list[dict[str, Any]] = []
        for job in self._jobs:
            entry: dict[str, Any] = dict(job.snapshot())
            entry["interval_sec"] = intervals.get(job.name)
            entry["tick_timeout_sec"] = TICK_TIMEOUTS.get(job.name)
            # leader 身份：选主锁值 "{name}:{instance_id}:{uuid4}"（TTL=interval，
            # tick 间隙可能瞬时缺 key → leader=None 属正常）
            lock_key = f"agent_runtime:job:{job.name}"
            token = await self.redis.get(lock_key)
            if token:
                token = token.decode() if isinstance(token, (bytes, bytearray)) else str(token)
                leader = token.removeprefix(f"{job.name}:").rsplit(":", 1)[0]
                entry["leader"] = {
                    "instance_id": leader,
                    "is_local": leader == self.instance_id,
                    "ttl_sec": int(await self.redis.ttl(lock_key) or 0),
                }
            else:
                entry["leader"] = None
            out.append(entry)
        return out

    async def stop(self) -> None:
        for job in self._jobs:
            try:
                await job.stop()
            except Exception:  # noqa: BLE001
                self.logger.exception("job stop failed: %s", job)
        self._jobs = []
        try:
            await self.k8s.close()
        except Exception:  # noqa: BLE001
            self.logger.exception("kubernetes client close failed")
        await self.rm_sysctx.stop()
        await super().stop()
        self.logger.info("agent-runtime stopped: instance=%s", self.instance_id)


def create_app(
    settings: ServiceConfig,
    arc: AgentRuntimeConfig,
    *,
    resources: tuple[Any, Any, Any] | None = None,
    instance_id: str | None = None,
    own_resources: bool = True,
) -> App:
    """构造唯一 App（/api/session）并注册 4 个对外 handler。

    resources / instance_id / own_resources 供多实例测试注入共享物理资源
    （同一 redis/db/k8s、显式 instance_id、不重复持有资源生命周期）；
    生产路径不传，行为与原先完全一致。
    """

    def ctx_factory() -> OrchestratorSystemContext:
        if resources is not None:
            redis_client, db, k8s = resources
        else:
            redis_client, db, k8s = build_resources(settings, arc)
        return OrchestratorSystemContext(
            redis_client=redis_client, db=db, k8s=k8s,
            settings=settings, arc=arc,
            instance_id=instance_id,
            owns_resources=own_resources,
        )

    app = App(ctx_factory, prefix=SERVICE_PREFIX, enable_ws=False, title="agent-runtime")
    # 请求汇总日志 + 指标（touch/cleanup 从此每请求一行；registry 供 /debug/stats）
    registry = MetricsRegistry()
    app.use(request_metrics_middleware(registry))
    app.asgi.state.metrics = registry
    _register_healthz(app)
    register_debug_api(app, registry=registry)
    register_handlers(app)
    return app


def _register_healthz(app: App) -> None:
    """GET /healthz：进程就绪探针（K8s 探针 / 多进程就绪轮询 / e2e 实例观测）。

    lifespan 完成即 sysctx 就绪（启动期 fail-fast：Redis/DB 不可用直接起不来），
    因此「state.sysctx 存在」== 进程可服务；未就绪返回 503。
    附带 instance_id 便于经 LB 观测后端实例身份。

    注：main.py 顶部有 ``from __future__ import annotations``，字符串注解经
    ``get_type_hints`` 用**模块全局**解析 —— Request 必须顶层导入，函数内
    局部导入会被 FastAPI 当成 query 参数（422）。
    """

    @app.asgi.get("/healthz", summary="liveness/readiness probe")
    async def _healthz(request: Request):  # noqa: ANN202
        sysctx = getattr(request.app.state, "sysctx", None)
        if sysctx is None:
            return JSONResponse(status_code=503, content={"ok": False})
        return {"ok": True, "instance_id": sysctx.instance_id}
