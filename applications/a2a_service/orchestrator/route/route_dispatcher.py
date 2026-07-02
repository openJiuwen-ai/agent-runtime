from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Set

from loguru import logger

from .handler_registry import HandlerRegistry
from .normalized_event import NormalizedEvent, RouteContext, RouteTarget
from .route_profiles import RouteConfig, RouteConfigLoader, SourceRouteProfile
from .route_strategies import (
    LocalAgentSourceStrategy,
    RemoteAgentSourceStrategy,
    RequesterSourceStrategy,
    RouteStrategy,
)
from ..state.task_state_manager import TaskStateManager


class RouteDispatcher:
    def __init__(
        self,
        state_mgr: TaskStateManager,
        config: Optional[RouteConfig] = None,
        config_path: Optional[str] = None,
        local_agent_names: Optional[list[str]] = None,
    ):
        self._state_mgr = state_mgr
        self._strategy_cache: Dict[str, Dict[str, RouteStrategy]] = {}
        self._handlers: Dict[str, Dict[Optional[str], Callable]] = {}
        self._handler_instances: Dict[str, object] = {}
        self._local_agent_keys: Set[str] = set(local_agent_names or [])

        if config_path:
            self._config = RouteConfigLoader.load(config_path)
        else:
            self._config: RouteConfig = config or RouteConfig()

        self._default_profile = self._config.default_profile
        self._profiles: Dict[str, SourceRouteProfile] = self._config.profiles

    @property
    def config(self) -> RouteConfig:
        return self._config

    def load_config(self, config_path: str) -> Dict[str, str]:
        """从 yaml 文件加载路由配置

        Returns:
            处理器配置字典 {target_type: handler_class_path}
        """
        self._config = RouteConfigLoader.load(config_path)
        self._default_profile = self._config.default_profile
        self._profiles = self._config.profiles
        self._strategy_cache.clear()
        self._ensure_strategies("")
        return self._config.handlers

    def register_handlers_from_config(self, **kwargs) -> None:
        """从配置中的 handlers 字段动态加载并注册处理器插件

        Args:
            **kwargs: 传递给处理器构造函数的参数（如 state_manager）
        """
        if not self._config.handlers:
            return

        registry = HandlerRegistry()
        handlers = registry.load_handlers(self._config.handlers, **kwargs)

        for target_type, handler_instance in handlers.items():
            self._handler_instances[target_type] = handler_instance
            self.register_handler(target_type, handler_instance.handle)
            logger.debug(
                f"[RouteDispatcher] 注册处理器: {target_type}"
            )

    def get_handler_instance(self, target_type: str) -> object | None:
        return self._handler_instances.get(target_type)

    def get_profile(self, agent_key: str) -> SourceRouteProfile:
        return self._profiles.get(agent_key, self._default_profile)

    def _ensure_strategies(self, agent_key: str) -> Dict[str, RouteStrategy]:
        if agent_key in self._strategy_cache:
            return self._strategy_cache[agent_key]

        profile = self.get_profile(agent_key)
        strategies = {
            "requester": RequesterSourceStrategy(
                self._state_mgr, profile.requester, self._local_agent_keys, self._config.max_cascade_depth
            ),
            "local_agent": LocalAgentSourceStrategy(profile.local_agent),
            "remote_agent": RemoteAgentSourceStrategy(profile.remote_agent),
        }
        self._strategy_cache[agent_key] = strategies
        return strategies

    def register_handler(
        self, target_type: str, handler: Callable, source: Optional[str] = None
    ):
        if target_type not in self._handlers:
            self._handlers[target_type] = {}
        self._handlers[target_type][source] = handler

    async def route(self, event: NormalizedEvent, context: RouteContext) -> RouteTarget:
        source = event.metadata.get("source", "requester")
        agent_key = context.agent_key or ""
        strategies = self._ensure_strategies(agent_key)

        strategy = strategies.get(source)
        if not strategy:
            raise ValueError(f"Unknown source direction: {source}")
        
        target = await strategy.route(event, context)
        
        # 关键日志：记录路由决策结果（源 -> 目标）
        logger.info(
            f"[RouteDispatcher] 路由决策: event_type={event.type}, "
            f"source={source} -> target={target.type}, agent_key={target.agent_key}"
        )
        
        return target

    async def dispatch(self, event: NormalizedEvent, context: Dict[str, Any]) -> Any:
        route_context = RouteContext(
            task_id=context.get("task_id", ""),
            current_task=context.get("current_task"),
            conv_id=context.get("conv_id", ""),
            root_task_id=context.get("root_task_id", ""),
            agent_key=context.get("agent_key", ""),
            is_specify_task=context.get("is_specify_task", True),
        )
        target = await self.route(event, route_context)

        target_handlers = self._handlers.get(target.type)
        if not target_handlers:
            raise ValueError(f"No handler registered for target type: {target.type}")

        event_source = event.metadata.get("source")
        handler = target_handlers.get(event_source) or target_handlers.get(None)

        if not handler:
            raise ValueError(
                f"No handler registered for target type '{target.type}' "
                f"with source '{event_source}'."
            )

        return await handler(event, target, context)
