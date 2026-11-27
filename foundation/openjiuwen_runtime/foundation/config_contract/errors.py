# coding: utf-8
"""配置契约校验的共享错误类型与可注入错误工厂。

两个镜像对「校验失败」的错误契约不同：

- Manager：只需要错误文案（路由层统一映射 400），使用默认的
  ``ConfigContractError``（``ValueError`` 子类）；
- Agent Runtime：要求沿用既有 ``agent_runtime.errors.InvalidParams``（带业务
  错误码 ``VALIDATION``，HTTP 400），由 ``agent_runtime`` 的兼容层在 import
  时通过 ``set_error_factory`` 注入。

``fail`` 在调用时读取当前工厂，因此 import 顺序不影响结果。
"""

from __future__ import annotations

from typing import Callable, NoReturn


class ConfigContractError(ValueError):
    """配置契约校验失败（foundation 默认错误类型）。"""


ErrorFactory = Callable[[str], BaseException]

_error_factory: ErrorFactory = ConfigContractError


def set_error_factory(factory: ErrorFactory) -> None:
    """替换校验失败的异常类型（须为异常类；Runtime 兼容层注入 InvalidParams）。"""
    global _error_factory
    _error_factory = factory


def error_factory() -> ErrorFactory:
    return _error_factory


def fail(message: str) -> NoReturn:
    """抛出当前生效的校验异常。"""
    raise _error_factory(message)


def is_contract_error(exc: BaseException) -> bool:
    """判断异常是否来自本包的校验（兼容注入后的自定义异常类型）。"""
    if isinstance(exc, ConfigContractError):
        return True
    factory = _error_factory
    return isinstance(factory, type) and isinstance(exc, factory)
