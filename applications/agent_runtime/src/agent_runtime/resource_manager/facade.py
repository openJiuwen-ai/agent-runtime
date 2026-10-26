# coding: utf-8
"""ResourceManagerFacade：SM → RM 的进程内调用入口。

方法与错误码契约见 SM 设计 §13.1 / RM 设计 §2：
- acquire(scope_id, pod_spec, pool_config, request_id) → {pod_id, pod_sse_url}；
  失败抛 MaxPodsReached / DeployFailed / PodsStartingUp（SM route 捕获映射
  NO_POD_AVAILABLE；PodsStartingUp=新 Pod 冷启动中，非封顶）。
- idle_consider(pod_id, scope_id) → {transitioned_to_idle: bool}，幂等。
- update_pool_config(scope_id, pool_config, pod_spec?) → {updated: bool}（config_sync 触发）。
- bump_generation(scope_id) → int（config_refresh 触发的代次日落，新代次返回值）。
- sunset_pending_pods(scope_id) → list[str]（config_refresh 前置闸门：代次落后的
  注册 Pod；非空 = 上一轮日落未完成，refresh 409 串行化）。
- cleanup(namespace?, label_selector?) → int（运维批删，经 SM 的 /cleanup 委托）。
- register_pods / deregister_pods / declared_pods / drop_declared（routing 供给
  模式，2026-10）：外部预建 Pod 的声明/注册/注销；register/deregister 经 SM 的
  /register_pods / /deregister_pods 委托，declared_pods 供 SM 软摘除豁免查询，
  drop_declared 供 config_store scope 删除路径清声明（豁免解除）。
"""

from __future__ import annotations

from .orchestrator import ResourceOrchestrator


class ResourceManagerFacade:
    """供 session_manager 模块进程内调用的 RM 能力（薄封装，逻辑在 orchestrator）。"""

    def __init__(self, orchestrator: ResourceOrchestrator) -> None:
        self._orchestrator = orchestrator
        self.state = orchestrator.state

    async def acquire(
        self,
        scope_id: str,
        pod_spec: dict,
        pool_config: dict,
        request_id: str = "",
    ) -> dict[str, str]:
        return await self._orchestrator.acquire(
            scope_id=scope_id, pod_spec=pod_spec,
            pool_config=pool_config, request_id=request_id,
        )

    async def idle_consider(self, pod_id: str, scope_id: str) -> dict[str, bool]:
        return await self._orchestrator.idle_consider(pod_id=pod_id, scope_id=scope_id)

    async def update_pool_config(
        self,
        scope_id: str,
        pool_config: dict,
        pod_spec: dict | None = None,
    ) -> dict[str, bool]:
        return await self._orchestrator.update_pool_config(
            scope_id=scope_id, pool_config=pool_config, pod_spec=pod_spec,
        )

    async def bump_generation(self, scope_id: str) -> int:
        """config_refresh 触发：scope 代次 +1（HINCRBY 原子），返回新代次。"""
        return await self._orchestrator.bump_generation(scope_id=scope_id)

    async def sunset_pending_pods(self, scope_id: str) -> list[str]:
        """config_refresh 前置闸门：代次落后于当前配置的注册 Pod（含排空中）。

        非空 → 上一轮 refresh 的日落尚未完成，refresh 应 409 拒绝（串行化：
        防多代日落堆积蹲占 max_pods）。
        """
        return await self._orchestrator.sunset_pending_pods(scope_id=scope_id)

    async def cleanup(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> int:
        return await self._orchestrator.cleanup(namespace=namespace,
                                                label_selector=label_selector)

    async def known_scope_ids(self) -> list[str]:
        """RM 已知的全部 scope（scope:config 键枚举）。

        config_sync 的「被删 scope drain 收敛」依赖：DB 删行后即失忆，RM 侧
        config 键仍在才是幻影预热的真源（见 ConfigStore 扩散③）。
        """
        return await self._orchestrator.known_scope_ids()

    # -------------------------------------------------------------- 外部供给（routing 模式）

    async def register_pods(
        self, scope_id: str, pods: list[dict] | None,
    ) -> dict:
        return await self._orchestrator.register_pods(scope_id=scope_id, pods=pods)

    async def deregister_pods(
        self, scope_id: str, pods: list[str] | None,
    ) -> dict:
        return await self._orchestrator.deregister_pods(
            scope_id=scope_id, pods=pods)

    async def declared_pods(self, scope_id: str) -> list[str]:
        """该 scope 声明的外部 Pod 名（SM 软摘除豁免查询；full 模式恒空）。"""
        return await self.state.declared_names(scope_id)

    async def drop_declared(self, scope_id: str) -> int:
        """清空声明表（scope 删除/路由性排除）：外部 Pod 失去版本豁免，
        回归既有 gen-lag 日落回收（purge 仍不物理删）。"""
        return await self.state.drop_declared(scope_id)
