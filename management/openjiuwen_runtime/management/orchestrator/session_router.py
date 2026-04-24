# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Session 路由器模块 - 管理 session 到 service 的映射"""

from enum import Enum
from typing import Dict, Optional, Set, TYPE_CHECKING

from openjiuwen_runtime.foundation.log import get_logger

if TYPE_CHECKING:
    from .service_handler import ServiceHandler

logger = get_logger(__name__)


class RoutingStrategy(str, Enum):
    """路由策略"""
    STRICT = "strict"  # 严格亲和：session 必须路由到同一服务
    LOOSE = "loose"    # 宽松亲和：优先路由到同一服务，容量不足时可迁移
    NONE = "none"      # 无亲和：总是选择最优服务


class SessionRouter:
    """Session 路由器，管理 session 到 service 的映射"""

    def __init__(
        self,
        strategy: RoutingStrategy = RoutingStrategy.LOOSE,
    ):
        self._strategy = strategy
        self._session_to_service: Dict[str, str] = {}  # session_id -> deployment_id
        self._service_sessions: Dict[str, Set[str]] = {}  # deployment_id -> set of session_ids
        logger.info(f"SessionRouter initialized with strategy={strategy}")

    @property
    def strategy(self) -> RoutingStrategy:
        return self._strategy

    def get_service_for_session(
        self,
        session_id: str,
        services: Dict[str, "ServiceHandler"],
    ) -> Optional[str]:
        """
        获取 session 应该路由到的服务

        Args:
            session_id: 会话 ID
            services: 服务字典 {deployment_id: ServiceHandler}

        Returns:
            deployment_id 或 None
        """
        if self._strategy == RoutingStrategy.NONE:
            return self._find_best_service(session_id, services)

        deployment_id = self._session_to_service.get(session_id)
        if deployment_id and deployment_id in services:
            handler = services[deployment_id]
            if handler.has_capacity():
                logger.debug(
                    f"Session routed to existing service: session_id='{session_id}', "
                    f"deployment_id='{deployment_id}'"
                )
                return deployment_id

            if self._strategy == RoutingStrategy.STRICT:
                logger.warning(
                    f"Service at capacity but strict affinity: session_id='{session_id}', "
                    f"deployment_id='{deployment_id}'"
                )
                return deployment_id

        return self._find_best_service(session_id, services)

    def _find_best_service(
        self,
        session_id: str,
        services: Dict[str, "ServiceHandler"],
    ) -> Optional[str]:
        """
        找到最优服务

        优先选择有空闲容量的服务，按当前并发度排序（选择最空闲的）
        """
        best_deployment_id = None
        best_capacity = -1

        for deployment_id, handler in services.items():
            if handler.has_capacity():
                available_capacity = handler.max_concurrency - handler.current_concurrency
                if available_capacity > best_capacity:
                    best_capacity = available_capacity
                    best_deployment_id = deployment_id

        if best_deployment_id:
            logger.debug(
                f"Session routed to best available service: session_id='{session_id}', "
                f"deployment_id='{best_deployment_id}', available_capacity={best_capacity}"
            )
        else:
            logger.warning(
                f"No service with available capacity: session_id='{session_id}'"
            )

        return best_deployment_id

    def register_session(self, session_id: str, deployment_id: str) -> None:
        """
        注册 session 到服务的映射

        Args:
            session_id: 会话 ID
            deployment_id: 部署 ID
        """
        old_deployment_id = self._session_to_service.get(session_id)
        if old_deployment_id and old_deployment_id != deployment_id:
            if old_deployment_id in self._service_sessions:
                self._service_sessions[old_deployment_id].discard(session_id)
            logger.debug(
                f"Session migrated: session_id='{session_id}', "
                f"old_deployment_id='{old_deployment_id}', new_deployment_id='{deployment_id}'"
            )

        self._session_to_service[session_id] = deployment_id

        if deployment_id not in self._service_sessions:
            self._service_sessions[deployment_id] = set()
        self._service_sessions[deployment_id].add(session_id)

        logger.debug(
            f"Session registered: session_id='{session_id}', deployment_id='{deployment_id}'"
        )

    def unregister_session(self, session_id: str) -> None:
        """
        取消注册 session

        Args:
            session_id: 会话 ID
        """
        deployment_id = self._session_to_service.pop(session_id, None)
        if deployment_id:
            if deployment_id in self._service_sessions:
                self._service_sessions[deployment_id].discard(session_id)
                if not self._service_sessions[deployment_id]:
                    del self._service_sessions[deployment_id]
            logger.debug(
                f"Session unregistered: session_id='{session_id}', deployment_id='{deployment_id}'"
            )

    def unregister_service(self, deployment_id: str) -> Set[str]:
        """
        取消注册服务的所有 session

        Args:
            deployment_id: 部署 ID

        Returns:
            被移除的 session_id 集合
        """
        session_ids = self._service_sessions.pop(deployment_id, set())
        for session_id in session_ids:
            self._session_to_service.pop(session_id, None)

        if session_ids:
            logger.debug(
                f"Service sessions unregistered: deployment_id='{deployment_id}', "
                f"session_count={len(session_ids)}"
            )

        return session_ids

    def get_service_sessions(self, deployment_id: str) -> Set[str]:
        """
        获取服务上的所有 session

        Args:
            deployment_id: 部署 ID

        Returns:
            session_id 集合
        """
        return self._service_sessions.get(deployment_id, set()).copy()

    def get_session_service(self, session_id: str) -> Optional[str]:
        """
        获取 session 所在的服务

        Args:
            session_id: 会话 ID

        Returns:
            deployment_id 或 None
        """
        return self._session_to_service.get(session_id)

    def get_service_session_count(self, deployment_id: str) -> int:
        """
        获取服务上的 session 数量

        Args:
            deployment_id: 部署 ID

        Returns:
            session 数量
        """
        return len(self._service_sessions.get(deployment_id, set()))

    def clear(self) -> None:
        """清除所有映射"""
        self._session_to_service.clear()
        self._service_sessions.clear()
        logger.info("SessionRouter cleared")
