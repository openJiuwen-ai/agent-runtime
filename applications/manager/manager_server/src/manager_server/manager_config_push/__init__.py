# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager → Gateway / Runtime HTTP 配置下发。

业务侧调用 ``gateway_request`` / ``runtime_request``；
公用传输见 ``client.http_request``，endpoint 解析见 ``endpoint`` 模块。
"""

from __future__ import annotations

from manager_server.manager_config_push.client import (
    gateway_request,
    http_request,
    runtime_request,
)
from manager_server.manager_config_push.endpoint import (
    list_reachable_jiuwenclaw_ids,
    require_gateway_endpoint,
    require_runtime_endpoint,
)

__all__ = (
    "gateway_request",
    "http_request",
    "list_reachable_jiuwenclaw_ids",
    "require_gateway_endpoint",
    "require_runtime_endpoint",
    "runtime_request",
)
