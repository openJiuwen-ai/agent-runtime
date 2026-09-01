# coding: utf-8
"""ResourceManagerFacade：SM → RM 的进程内调用入口。

方法与错误码契约见 SM 设计 §13.1 / RM 设计 §2：
- acquire(scope_id, pod_spec, pool_config, request_id) → {pod_id, pod_sse_url}；
  失败抛 MaxPodsReached / DeployFailed（SM route 捕获映射 NO_POD_AVAILABLE）。
- idle_consider(pod_id, scope_id) → {transitioned_to_idle: bool}，幂等。
- update_pool_config(scope_id, pool_config, pod_spec?) → {updated: bool}（config_sync 触发）。
- bump_generation(scope_id) → int（config_refresh 触发的代次日落，新代次返回值）。
- cleanup(namespace?, label_selector?) → int（运维批删，经 SM 的 /cleanup 委托）。
"""

from __future__ import annotations

from .orchestrator import ResourceOrchestrator


class ResourceManagerFacade:
    """供 session_manager 模块进程内调用的 RM 能力（薄封装，逻辑在 orchestrator）。"""

    def __init__(self, orchestrator: ResourceOrchestrator) -> None:
        self._orchestrator = orchestrator

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
