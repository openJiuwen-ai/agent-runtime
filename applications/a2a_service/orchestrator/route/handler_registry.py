from __future__ import annotations

import inspect
import importlib
from typing import Dict, Type

from loguru import logger


class HandlerRegistry:
    """处理器注册表（插件化加载核心）

    支持从配置动态加载处理器插件，实现核心框架与业务逻辑解耦。
    """

    def __init__(self):
        self._handler_classes: Dict[str, Type] = {}

    def load_handlers(self, handler_config: Dict[str, str], **kwargs) -> Dict[str, object]:
        """从配置动态加载处理器类

        Args:
            handler_config: 处理器配置 {target_type: class_path}
            **kwargs: 传递给处理器构造函数的参数（自动过滤非匹配参数）

        Returns:
            处理器实例映射 {target_type: handler_instance.handle}
        """
        handlers: Dict[str, object] = {}

        for target_type, class_path in handler_config.items():
            try:
                module_path, class_name = class_path.rsplit(".", 1)
                module = importlib.import_module(module_path)
                handler_class = getattr(module, class_name)

                filtered_kwargs = self._filter_kwargs(handler_class, kwargs)
                handler_instance = handler_class(**filtered_kwargs)
                handlers[target_type] = handler_instance
                logger.debug(
                    f"[HandlerRegistry] 加载处理器: {target_type} -> {class_path}"
                )
            except Exception as e:
                logger.error(
                    f"[HandlerRegistry] 加载处理器失败: {target_type} -> {class_path}, "
                    f"error={e}"
                )

        return handlers

    @staticmethod
    def _filter_kwargs(handler_class: Type, kwargs: Dict) -> Dict:
        """根据处理器构造函数的签名过滤参数，只传递构造函数接受的参数"""
        try:
            sig = inspect.signature(handler_class.__init__)
            params = sig.parameters

            if any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in params.values()
            ):
                return kwargs

            accepted = set(params.keys()) - {"self"}
            return {k: v for k, v in kwargs.items() if k in accepted}
        except Exception:
            return kwargs

    def register_handler_class(self, target_type: str, handler_class: Type):
        """注册处理器类（编程式注册，用于扩展）"""
        self._handler_classes[target_type] = handler_class
