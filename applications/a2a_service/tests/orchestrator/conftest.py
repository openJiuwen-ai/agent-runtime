# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Orchestrator test bootstrap helpers."""
from __future__ import annotations

import importlib.util
import sys
import types
from typing import Any, AsyncIterator


if "agents.EDPAgent" not in sys.modules and importlib.util.find_spec("agents.EDPAgent") is None:
    pkg_agents = sys.modules.get("agents") or types.ModuleType("agents")
    sys.modules.setdefault("agents", pkg_agents)

    edp_module = types.ModuleType("agents.EDPAgent")

    async def _agent_stream_stub(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        if False:
            yield  # pragma: no cover
        return

    edp_module.agent_stream = _agent_stream_stub  # type: ignore[attr-defined]
    edp_module.get_otel_tracer = lambda: None  # type: ignore[attr-defined]  # OTel 默认关闭（编排层 span 降级为空操作）
    # 编排层 span 的 cm 由 EDPAgent.otel_span_helper 提供，注入同签名桩（默认 tracer 未注入 → yield None）
    from tests.otel_span_helper_stub import install_otel_span_helper_stub

    install_otel_span_helper_stub(edp_module)
    sys.modules["agents.EDPAgent"] = edp_module
    setattr(pkg_agents, "EDPAgent", edp_module)
