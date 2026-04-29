# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Stub agents.EDPAgent for all subtree tests.

orchestrator.user_router → orchestrator.executor → agents.EDPAgent.agent_stream
该链路在运行时由 agent-store 同步过来；脱机单测里 EDPAgent 目录为空，导入会断。

把占位提到 tests/ 级别，让 orchestrator/ 和 integration/ 子树都能继承。
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
