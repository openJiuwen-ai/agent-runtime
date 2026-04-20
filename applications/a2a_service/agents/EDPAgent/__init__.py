"""
EDPAgent — 企业动态规划智能体。

公开接口：
  from agents.EDPAgent import initialize, agent_stream

零 A2A 依赖：本模块不依赖 a2a-sdk，可在任意 Python 环境中使用。
"""
from __future__ import annotations

from .agent import initialize_dpa as initialize, agent_stream

__all__ = ["initialize", "agent_stream"]
