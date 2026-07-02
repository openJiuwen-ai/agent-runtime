# coding: utf-8
# pylint: disable=protected-access
# 说明：本文件为单元测试，需要访问 A2aVersatileExecutor 的 protected 成员
# （_extract_runner_kwargs / _make_text_part / _extract_logging_context 等）。
"""A2aVersatileExecutor 薄壳适配层测试。

按 TECH_VersatileAdapter.md §3.4 验证：
- _extract_runner_kwargs：target 路由信息提取（含 conversation_id 回退）
- _build_first_input：从 A2A Message 解析 input_data
- AdapterEvent → A2A 事件映射逻辑（data_proxy / execution_completed / execution_input_required）
"""
from __future__ import annotations

import pytest

from a2a.types.a2a_pb2 import TASK_STATE_INPUT_REQUIRED
from a2a_facade.executor import A2aVersatileExecutor
from dispatcher.runner import VersatileAdapterRunner
from loguru import logger


class _EventQueueStub:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


# ════════════════════════════════════════════════════════════════════
# _extract_runner_kwargs：target / body / headers / params 提取
# ════════════════════════════════════════════════════════════════════


class TestExtractRunnerKwargs:
    @staticmethod
    def test_target_with_conversation_id():
        input_data = {
            "target": {"conversation_id": "c-1", "intent": "qa"},
            "body": {"input": {"query": "q"}},
            "headers": {"X-User-Id": "u1"},
            "params": {"workspace_id": "10"},
        }
        kw = A2aVersatileExecutor._extract_runner_kwargs(input_data, "fallback-conv")
        assert kw["target"]["conversation_id"] == "c-1"
        assert kw["target"]["intent"] == "qa"
        assert kw["body"] == {"input": {"query": "q"}}
        assert kw["headers"] == {"X-User-Id": "u1"}
        assert kw["params"] == {"workspace_id": "10"}

    @staticmethod
    def test_target_without_conversation_id_falls_back_to_context_id():
        """target 中无 conversation_id 时回退使用 A2A context_id。"""
        input_data = {
            "target": {"intent": "qa"},
            "body": {},
            "headers": {},
            "params": {},
        }
        kw = A2aVersatileExecutor._extract_runner_kwargs(input_data, "ctx-conv-1")
        assert kw["target"]["conversation_id"] == "ctx-conv-1"
        assert kw["target"]["intent"] == "qa"

    @staticmethod
    def test_empty_input_data():
        kw = A2aVersatileExecutor._extract_runner_kwargs({}, "conv-x")
        assert kw["target"]["conversation_id"] == "conv-x"
        assert kw["body"] == {}
        assert kw["headers"] == {}
        assert kw["params"] == {}

    @staticmethod
    def test_target_with_workflow_id():
        input_data = {
            "target": {"workflow_id": "wf-wealth", "conversation_id": "c-2"},
            "body": {},
            "headers": {},
            "params": {},
        }
        kw = A2aVersatileExecutor._extract_runner_kwargs(input_data, "ignored")
        assert kw["target"]["workflow_id"] == "wf-wealth"


# ════════════════════════════════════════════════════════════════════
# _make_text_part：Part 构造
# ════════════════════════════════════════════════════════════════════


class TestMakeTextPart:
    @pytest.fixture
    def executor(self):
        # 最小 runner mock（不需要真实路由能力）
        return A2aVersatileExecutor(runner=None)

    @staticmethod
    def test_part_with_vatype_metadata(executor):
        part = executor._make_text_part("hello", vatype="data_proxy")
        assert part.text == "hello"
        from google.protobuf.json_format import MessageToDict
        meta = MessageToDict(part.metadata)
        assert meta.get("vatype") == "data_proxy"

    @staticmethod
    def test_part_without_metadata(executor):
        part = executor._make_text_part("hello", vatype=None)
        assert part.text == "hello"
        assert not part.HasField("metadata") or part.metadata == part.metadata.__class__()


# ════════════════════════════════════════════════════════════════════
# _extract_logging_context
# ════════════════════════════════════════════════════════════════════


class TestExtractLoggingContext:
    @staticmethod
    def test_extracts_trace_and_agent_id():
        input_data = {"trace_id": "t-1", "agent_id": "a-1", "other": "x"}
        ctx = A2aVersatileExecutor._extract_logging_context(input_data, "conv-1")
        assert ctx["trace_id"] == "t-1"
        assert ctx["agent_id"] == "a-1"
        assert ctx["conv_id"] == "conv-1"

    @staticmethod
    def test_missing_fields_default_empty():
        ctx = A2aVersatileExecutor._extract_logging_context({}, "conv-1")
        assert ctx["trace_id"] == ""
        assert ctx["agent_id"] == ""


# ════════════════════════════════════════════════════════════════════
# INPUT_REQUIRED 日志语义
# ════════════════════════════════════════════════════════════════════


class TestInputRequiredLogging:
    @staticmethod
    @pytest.mark.asyncio
    async def test_input_required_emits_state_event_without_warning_log():
        executor = A2aVersatileExecutor(runner=None)
        queue = _EventQueueStub()
        records = []
        sink_id = logger.add(
            lambda message: records.append(message.record),
            level="DEBUG",
            format="{message}",
        )
        try:
            await executor._emit_input_required(
                queue,
                task_id="task-1",
                context_id="ctx-1",
                text="等待用户输入",
            )
        finally:
            logger.remove(sink_id)

        assert len(queue.events) == 1
        event = queue.events[0]
        assert event.status.state == TASK_STATE_INPUT_REQUIRED
        assert event.status.message.parts[0].text == "等待用户输入"

        messages = [r["message"] for r in records]
        assert any("A2A_INFO:VA_INPUT_REQUIRED state=INPUT_REQUIRED" in m for m in messages)
        assert not any(
            "A2A_WARNING:VA_TERMINAL_FALLBACK state=INPUT_REQUIRED" in m
            for m in messages
        )
        assert all(
            r["level"].name != "WARNING"
            or "state=INPUT_REQUIRED" not in r["message"]
            for r in records
        )
