# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""manager_config_push 单元测试（gateway_request 导出）。"""

from __future__ import annotations

import inspect

from manager_server.manager_config_push import gateway_request
from manager_server.manager_config_push.client import gateway_request as client_gateway_request


def test_gateway_request_exported() -> None:
    assert gateway_request is client_gateway_request
    assert inspect.iscoroutinefunction(gateway_request)
    sig = inspect.signature(gateway_request)
    assert "jiuwenclaw_id" in sig.parameters
    assert "method" in sig.parameters
    assert "path" in sig.parameters
    assert "business" in sig.parameters
    assert "revision" not in sig.parameters
    assert "enc_section" not in sig.parameters
    assert "enc_wrap" not in sig.parameters
