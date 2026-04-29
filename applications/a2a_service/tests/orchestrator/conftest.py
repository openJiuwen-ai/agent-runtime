# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Stub agents.EDPAgent for orchestrator unit tests.

orchestrator.user_router → orchestrator.executor → agents.EDPAgent.agent_stream
该链路在运行时由 agent-store 同步过来；脱机单测里 EDPAgent 目录为空，导入会断。

本 conftest 在 collection 之前注入一个最小的 ``agent_stream`` 占位实现，让
orchestrator 模块图能完整加载。占位本身不会被任何 _build_request /
_extract_query_params 的契约测试触发，只是为了打通 import 链。
"""
from __future__ import annotations

import sys
import types
from typing import Any, AsyncIterator


if "agents.EDPAgent" not in sys.modules:
    pkg_agents = sys.modules.get("agents") or types.ModuleType("agents")
    sys.modules.setdefault("agents", pkg_agents)

    edp_module = types.ModuleType("agents.EDPAgent")

    async def _agent_stream_stub(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        if False:
            yield  # pragma: no cover  - 仅为函数成为 async generator
        return

    edp_module.agent_stream = _agent_stream_stub  # type: ignore[attr-defined]
    sys.modules["agents.EDPAgent"] = edp_module
    setattr(pkg_agents, "EDPAgent", edp_module)
