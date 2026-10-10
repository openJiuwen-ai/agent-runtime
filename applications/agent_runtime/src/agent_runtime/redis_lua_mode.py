# coding: utf-8
"""进程级 Redis Lua / no-Lua 模式默认值（AGENT_RUNTIME_REDIS_LUA 传递链）。

开关语义（仅显式 on/off，无自动探测）：
- ``AGENT_RUNTIME_REDIS_LUA=on``（默认）：全部 Lua 路径，行为与存量逐字节一致；
- ``AGENT_RUNTIME_REDIS_LUA=off``：SM/RM 状态层走 WATCH/MULTI/EXEC 无 Lua
  实现，框架层（锁/选主）同步 memo（禁 EVAL 环境不发任何探测命令）。

生产链路：``OrchestratorSystemContext.start()`` 依据 ``AgentRuntimeConfig``
置位（早于任何 eval）；``SessionState/ResourceState`` 构造时不决策，首次
eval 惰性读取并固化——测试/verify 脚本可直接 ``use_lua=False`` 显式指定，
也可 ``set_default()`` 模拟进程级配置。未设置默认 → lua（存量行为）。
"""

from __future__ import annotations

# None = 未设置（= lua，存量行为；进程级默认仅在 start() 设置）
_default_use_lua: bool | None = None


def set_default(use_lua: bool) -> None:
    """进程级置位（start() 依据配置调用；测试亦可直接设）。"""
    global _default_use_lua
    _default_use_lua = bool(use_lua)


def get_default() -> bool | None:
    """读进程级默认；None = 未设置（调用方按 lua 处理）。"""
    return _default_use_lua


def clear_default() -> None:
    """清除进程级默认（测试隔离用，恢复"未设置=lua"存量语义）。"""
    global _default_use_lua
    _default_use_lua = None


__all__ = ["clear_default", "get_default", "set_default"]
