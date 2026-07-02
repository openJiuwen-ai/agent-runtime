from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set

from loguru import logger

from .normalized_event import NormalizedEvent, RouteContext, RouteTarget
from .route_profiles import (
    RequesterSourceProfile,
    LocalAgentSourceProfile,
    RemoteAgentSourceProfile,
)
from ..state.task_state_manager import (
    TaskStateManager,
    META_KEY_SOURCE_AGENT,
    META_KEY_SUB_TASKS,
    META_KEY_REMOTE_TASK_ID,
)


class RouteStrategy(ABC):
    @abstractmethod
    async def route(self, event: NormalizedEvent, context: RouteContext) -> RouteTarget:
        ...


class RequesterSourceStrategy(RouteStrategy):
    def __init__(
        self,
        state_mgr: TaskStateManager,
        profile: RequesterSourceProfile,
        local_agent_keys: Optional[Set[str]] = None,
        max_cascade_depth: int = 10,
    ):
        self._state_mgr = state_mgr
        self._profile = profile
        self._suspended_states: Set[str] = set(profile.suspended_states)
        self._local_agent_keys: Set[str] = local_agent_keys or set()
        self._max_cascade_depth: int = max_cascade_depth

    async def route(self, event: NormalizedEvent, context: RouteContext) -> RouteTarget:
        target_task_id = context.task_id or context.root_task_id
        task = await self._state_mgr.get_task(target_task_id)

        # 临时调试打印
        remote_task_id_debug = task.get("metadata", {}).get(META_KEY_REMOTE_TASK_ID, "") if task else ""
        status_state_debug = task.get("status_state", "") if task else "None"
        logger.info(
            f"[RequesterSourceStrategy] route debug: "
            f"task_id={context.task_id}, root_task_id={context.root_task_id}, "
            f"target_task_id={target_task_id}, remote_task_id={remote_task_id_debug}, "
            f"status_state={status_state_debug}, is_specify_task={context.is_specify_task}"
        )

        if task is None or task.get("status_state") not in self._suspended_states:
            return RouteTarget(type="local_agent")

        # 老版本兼容：未指定 task_id 时，仅通过 remote_task_id 判断是否路由到远程代理
        if not context.is_specify_task:
            remote_task_id = task.get("metadata", {}).get(META_KEY_REMOTE_TASK_ID, "")
            if remote_task_id:
                remote_task = await self._state_mgr.get_task(remote_task_id)
                if remote_task:
                    remote_source = remote_task.get("metadata", {}).get(META_KEY_SOURCE_AGENT, "")
                    return RouteTarget(type="remote_agent", agent_key=remote_source)
            return RouteTarget(type="local_agent")

        # 新版本：指定了 task_id，走级联路由逻辑
        source_agent = task.get("metadata", {}).get(META_KEY_SOURCE_AGENT, "")
        if source_agent in self._local_agent_keys:
            return RouteTarget(type="local_agent")

        route_path = await self._resolve_route_path(context)
        next_hop = self._determine_next_hop(route_path)
        return RouteTarget(type="remote_agent", agent_key=next_hop)

    async def _resolve_route_path(self, context: RouteContext) -> List[dict]:
        root_task_id = context.root_task_id
        root_task = await self._state_mgr.get_task(root_task_id)
        if not root_task:
            return []

        route_path = [
            {
                "task_id": root_task_id,
                "source_agent": root_task.get("metadata", {}).get(META_KEY_SOURCE_AGENT, ""),
            }
        ]
        target_task_id = context.task_id

        if not target_task_id or target_task_id == root_task_id:
            return route_path

        found = await self._bfs_find(root_task, target_task_id, route_path, depth=1)
        return found

    async def _bfs_find(
        self,
        parent_task: dict,
        target_task_id: str,
        path: List[dict],
        depth: int = 1,
    ) -> List[dict]:
        if depth > self._max_cascade_depth:
            return path

        sub_task_ids = parent_task.get("metadata", {}).get(META_KEY_SUB_TASKS, [])
        if not sub_task_ids:
            return path

        for sub_id in sub_task_ids:
            sub_task = await self._state_mgr.get_task(sub_id)
            if not sub_task:
                continue
            sub_path = path + [
                {
                    "task_id": sub_id,
                    "source_agent": sub_task.get("metadata", {}).get(META_KEY_SOURCE_AGENT, ""),
                }
            ]
            if sub_id == target_task_id:
                return sub_path
            result = await self._bfs_find(sub_task, target_task_id, sub_path, depth=depth + 1)
            if any(node["task_id"] == target_task_id for node in result):
                return result
        return path

    def _determine_next_hop(self, route_path: List[dict]) -> str:
        if not route_path:
            return ""
        for i in range(len(route_path) - 1, -1, -1):
            if route_path[i]["source_agent"] in self._local_agent_keys and i + 1 < len(route_path):
                return route_path[i + 1]["source_agent"]
        return route_path[-1].get("source_agent", "")


class LocalAgentSourceStrategy(RouteStrategy):
    def __init__(self, profile: LocalAgentSourceProfile):
        self._profile = profile
        self._delegate_types: Set[str] = set(profile.delegate_types)

    async def route(self, event: NormalizedEvent, context: RouteContext) -> RouteTarget:
        if event.type in self._delegate_types:
            agent_key = event.data.get("agent_key", self._profile.default_remote_agent)
            return RouteTarget(type="remote_agent", agent_key=agent_key)
        return RouteTarget(type="requester")


class RemoteAgentSourceStrategy(RouteStrategy):
    """Remote Agent 源路由策略

    NOTE: 当前业务代码中尚未有构建 NormalizedEvent(source=remote_agent) 的路径，
    VA 事件仍由 RemoteAgentHandler 直接 enqueue_event，未走 dispatch 路由。
    本策略为预留设计，待 VA 事件统一归一化后走 dispatch 时激活：
    - 终态帧（CONTROL_COMPLETED / CONTROL_FAILED）→ 路由到 local_agent
    - 非终态帧（DATA / CONTROL_WORKING 等）→ 路由到 channel
    """

    def __init__(self, profile: RemoteAgentSourceProfile):
        self._profile = profile
        self._terminal_frame_types: Set[str] = set(profile.terminal_frame_types)
        self._frame_type_map: Dict[str, str] = profile.frame_type_map

    async def route(self, event: NormalizedEvent, context: RouteContext) -> RouteTarget:
        frame_type = self._classify_frame(event)
        if frame_type in self._terminal_frame_types:
            return RouteTarget(type="local_agent")
        return RouteTarget(type="requester")

    def _classify_frame(self, event: NormalizedEvent) -> str:
        if "frame_type" in event.metadata:
            return event.metadata["frame_type"]
        return self._frame_type_map.get(event.type.upper(), self._profile.default_frame_type)
