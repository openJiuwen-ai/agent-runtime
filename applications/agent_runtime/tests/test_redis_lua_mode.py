# coding: utf-8
"""AGENT_RUNTIME_REDIS_LUA 开关与 State 门面分派骨架的单测。

（no_lua 各脚本实现的行为等价性见 tests/session_manager/test_sm_state_no_lua.py
与 tests/resource_manager/test_rm_state_no_lua.py 的双模式套件。）
"""

from __future__ import annotations

import pytest

from agent_runtime import redis_lua_mode
from agent_runtime.config import AgentRuntimeConfig
from agent_runtime.resource_manager.state import ResourceState
from agent_runtime.session_manager.state import SessionState

from .conftest import redis_client  # noqa: F401  (fixture)


def _set_env(monkeypatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("AGENT_RUNTIME_REDIS_LUA", raising=False)
    else:
        monkeypatch.setenv("AGENT_RUNTIME_REDIS_LUA", value)


@pytest.mark.parametrize("raw,expected", [
    (None, True), ("on", True), ("ON", True), ("1", True), ("true", True),
    ("off", False), ("OFF", False), ("0", False), ("false", False),
])
def test_config_redis_lua_parsing(monkeypatch, raw, expected):
    _set_env(monkeypatch, raw)
    assert AgentRuntimeConfig.from_env().redis_lua is expected


@pytest.mark.parametrize("raw", ["auto", "yes-please", "也许"])
def test_config_redis_lua_invalid_raises(monkeypatch, raw):
    _set_env(monkeypatch, raw)
    with pytest.raises(ValueError, match="仅接受 on\\|off"):
        AgentRuntimeConfig.from_env()


async def test_state_use_lua_explicit_param_short_circuits_default(
    redis_client,  # noqa: F811
):
    sm = SessionState(redis_client, use_lua=True)
    assert sm._lua_enabled() is True
    rm = ResourceState(redis_client, use_lua=False)
    assert rm._lua_enabled() is False


async def test_state_mode_falls_back_to_process_default_then_lua(redis_client):  # noqa: F811
    sm = SessionState(redis_client)
    try:
        # 未设置进程默认 → lua（存量行为）
        assert sm._lua_enabled() is True
        # 进程级 off（start() 链路）→ 新构造的 State 走 no-lua
        redis_lua_mode.set_default(False)
        sm2 = SessionState(redis_client)
        assert sm2._lua_enabled() is False
        # 已固化的 sm 不受后续 set_default 影响（构造期决议语义）
        assert sm._use_lua is True
    finally:
        redis_lua_mode.clear_default()


async def test_dispatch_unknown_script_raises(redis_client):  # noqa: F811
    sm = SessionState(redis_client, use_lua=False)
    with pytest.raises(RuntimeError, match="no-Lua 实现缺失"):
        await sm.eval("return 1 + 1", "arg")


async def test_dispatch_no_lua_path_taken(redis_client):  # noqa: F811
    """off 模式下 eval 出口走 no_lua 分派（行为级验证见双模式套件）。"""
    sm = SessionState(redis_client, use_lua=False)
    ok, pod = await sm.touch("s1", now=1000)
    assert ok is False and pod == ""
