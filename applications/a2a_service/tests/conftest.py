# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Test bootstrap helpers."""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
from typing import Any, AsyncIterator
from unittest.mock import MagicMock


class _OpenJiuwenStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        if fullname != "openjiuwen" and not fullname.startswith("openjiuwen."):
            return None
        if importlib.machinery.PathFinder.find_spec(fullname, path) is not None:
            return None
        return importlib.machinery.ModuleSpec(fullname, self, is_package=True)

    def create_module(self, spec):
        mod = types.ModuleType(spec.name)
        mock = MagicMock(name=spec.name)

        def _getattr(name, _m=mock):
            return getattr(_m, name)

        mod.__getattr__ = _getattr  # type: ignore[attr-defined]
        mod.__path__ = []
        return mod

    def exec_module(self, module) -> None:
        return None


if not any(isinstance(finder, _OpenJiuwenStubFinder) for finder in sys.meta_path):
    sys.meta_path.append(_OpenJiuwenStubFinder())


def _make_module(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        mock = MagicMock(name=name)

        def _getattr(attr, _m=mock):
            return getattr(_m, attr)

        mod.__getattr__ = _getattr  # type: ignore[attr-defined]
        mod.__path__ = []
        sys.modules[name] = mod
    return mod


state_mod = _make_module("openjiuwen.core.single_agent.interrupt.state")
state_mod.INTERRUPTION_KEY = "_interruption"


if "agents.EDPAgent" not in sys.modules and importlib.util.find_spec("agents.EDPAgent") is None:
    pkg_agents = sys.modules.get("agents") or types.ModuleType("agents")
    sys.modules.setdefault("agents", pkg_agents)

    edp_module = types.ModuleType("agents.EDPAgent")

    async def _agent_stream_stub(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        if False:
            yield  # pragma: no cover
        return

    edp_module.agent_stream = _agent_stream_stub  # type: ignore[attr-defined]
    sys.modules["agents.EDPAgent"] = edp_module
    setattr(pkg_agents, "EDPAgent", edp_module)
