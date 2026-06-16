# coding: utf-8
"""AdapterEvent 类型体系 + AgentCard 测试。

验证：
- AdapterEvent discriminated union 语义（同一时刻仅一个内容字段非 None）
- DataProxyContent / ExecutionCompletedContent / ExecutionInputRequiredContent
- AgentCard 基本属性
"""
from __future__ import annotations

import pytest

from event.events import (
    AdapterEvent,
    DataProxyContent,
    ExecutionCompletedContent,
    ExecutionInputRequiredContent,
)


class TestAdapterEvent:
    @staticmethod
    def test_data_proxy_event():
        e = AdapterEvent(data_proxy=DataProxyContent(raw_data='{"x":1}'))
        assert e.data_proxy is not None
        assert e.data_proxy.raw_data == '{"x":1}'
        assert e.execution_completed is None
        assert e.execution_input_required is None

    @staticmethod
    def test_execution_completed_event():
        e = AdapterEvent(
            execution_completed=ExecutionCompletedContent(
                is_failed=False, result="answer"
            )
        )
        assert e.execution_completed is not None
        assert e.execution_completed.result == "answer"
        assert e.execution_completed.is_failed is False
        assert e.data_proxy is None

    @staticmethod
    def test_execution_completed_failed():
        e = AdapterEvent(
            execution_completed=ExecutionCompletedContent(
                is_failed=True, result="error-msg"
            )
        )
        assert e.execution_completed.is_failed is True

    @staticmethod
    def test_execution_input_required_event():
        e = AdapterEvent(execution_input_required=ExecutionInputRequiredContent())
        assert e.execution_input_required is not None
        assert e.data_proxy is None
        assert e.execution_completed is None

    @staticmethod
    def test_default_all_none():
        e = AdapterEvent()
        assert e.data_proxy is None
        assert e.execution_completed is None
        assert e.execution_input_required is None

    @staticmethod
    def test_frozen_content():
        """DataProxyContent 是 frozen=True 的 Pydantic model。"""
        dp = DataProxyContent(raw_data="x")
        with pytest.raises(Exception):
            dp.raw_data = "y"  # type: ignore


class TestAgentCard:
    @staticmethod
    def test_card_basic_fields():
        from a2a_facade.agent_card import VERSATILE_ADAPTER_CARD
        assert VERSATILE_ADAPTER_CARD.name == "VersatileAdapter"
        assert VERSATILE_ADAPTER_CARD.version == "1.0.0"

    @staticmethod
    def test_card_has_streaming_capability():
        from a2a_facade.agent_card import VERSATILE_ADAPTER_CARD
        assert VERSATILE_ADAPTER_CARD.capabilities.streaming is True

    @staticmethod
    def test_card_has_skill():
        from a2a_facade.agent_card import VERSATILE_ADAPTER_CARD
        assert len(VERSATILE_ADAPTER_CARD.skills) >= 1
        assert VERSATILE_ADAPTER_CARD.skills[0].id == "execute_workflow"
