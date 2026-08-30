# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager → Gateway HTTP 配置下发（对齐 manager_config_receiver）。

业务侧直接调用 ``gateway_request``；本包仅提供 HTTP 客户端与 endpoint 解析。
"""

from __future__ import annotations

from manager_server.manager_config_push.client import gateway_request
from manager_server.manager_config_push.endpoint import (
    list_reachable_jiuwenclaw_ids,
    require_gateway_endpoint,
    resolve_gateway_endpoint,
)

__all__ = (
    "gateway_request",
    "list_reachable_jiuwenclaw_ids",
    "require_gateway_endpoint",
    "resolve_gateway_endpoint",
)
