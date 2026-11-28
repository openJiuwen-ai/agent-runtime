# coding: utf-8
"""兼容层:卷挂载规范形实现已迁至 ``foundation.config_contract``(Manager/Runtime 共用)。

保留原模块路径并再导出;导入时把共享校验的异常工厂切到 ``InvalidParams``,
维持 Runtime 既有 400 语义与测试断言(issue #203)。
"""

from openjiuwen_runtime.foundation.config_contract.errors import (
    set_error_factory,
)
from openjiuwen_runtime.foundation.config_contract.mounts import *  # noqa: F401,F403

from .errors import InvalidParams

set_error_factory(InvalidParams)
