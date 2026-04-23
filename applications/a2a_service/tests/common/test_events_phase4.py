"""
Phase 4 事件补齐单测（common/events.py + response_wrapper 交互）。

验证 SummaryEvent 与 FinalAnswerChunkEvent 的规范职责分工（方案 A）：
  - SummaryEvent = 流式片段（UI 边想边说）
  - FinalAnswerChunkEvent = 一次性全量文本（权威存档）
"""
from __future__ import annotations

import pytest

from common.events import (
    AgentEvent,
    EVENT_TYPE_MAP,
    FinalAnswerChunkEvent,
    FinalAnswerEndEvent,
    FinalAnswerStartEvent,
    SummaryEvent,
)
from common.response_wrapper import wrap_agent_event


# ════════════════════════════════════════════════════════════════════
# SummaryEvent 类型注册
# ════════════════════════════════════════════════════════════════════


def test_summary_event_registered():
    assert "summary" in EVENT_TYPE_MAP
    assert EVENT_TYPE_MAP["summary"] is SummaryEvent


def test_summary_event_construction():
    ev = SummaryEvent(content="已")
    assert ev.type == "summary"
    assert ev.content == "已"


# ════════════════════════════════════════════════════════════════════
# 方案 A 语义：summary 与 final_answer_chunk 并存、互补、不冲突
# ════════════════════════════════════════════════════════════════════


def test_summary_and_final_answer_chunk_are_distinct_types():
    s = SummaryEvent(content="已")
    c = FinalAnswerChunkEvent(content="完整总结文本")
    assert s.type == "summary"
    assert c.type == "final_answer_chunk"


def test_summary_stream_accumulates_to_final_answer_chunk():
    """典型流：多条 summary 流式 → 一条 final_answer_chunk 全量。"""
    stream_pieces = ["已", "为您完成如下事项", "：\n1.", " 理财产品推荐（完成）"]
    accumulated = "".join(stream_pieces)

    summaries = [SummaryEvent(content=p) for p in stream_pieces]
    final = FinalAnswerChunkEvent(content=accumulated)

    # summary 片段拼起来 = final_answer_chunk
    assert "".join(e.content for e in summaries) == final.content


# ════════════════════════════════════════════════════════════════════
# 与 wrapper 的交互：summary 走 Pattern A（含 execution_time 数字）
# ════════════════════════════════════════════════════════════════════


def test_summary_wrapped_has_numeric_execution_time():
    """summary 不在"think_chunk 空串"白名单里（决策 3）。"""
    wrapped = wrap_agent_event(
        event_type="summary",
        content="为您完成",
        data=None,
        agent_id="a",
        conversation_id="c",
        elapsed=19.89,
    )
    # summary 不是 think_chunk，execution_time 应是数字
    assert wrapped["execution_time"] == 19.89


def test_final_answer_chunk_wrapped_has_numeric_execution_time():
    wrapped = wrap_agent_event(
        event_type="final_answer_chunk",
        content="完整总结",
        data=None,
        agent_id="a",
        conversation_id="c",
        elapsed=21.88,
    )
    assert wrapped["execution_time"] == 21.88


# ════════════════════════════════════════════════════════════════════
# 抓包对齐：summary 流式帧样本
# ════════════════════════════════════════════════════════════════════


def test_matches_capture_summary_fragment():
    """抓包中典型 summary 帧（"已"、"为您完成如下事项" 等）。"""
    wrapped = wrap_agent_event(
        event_type="summary",
        content="为您完成如下事项",
        data=None,
        agent_id="agent-1",
        conversation_id="conv-1",
        elapsed=19.891351,
        created_time_ms=1776678994390,
    )
    assert wrapped["custom_rsp_data"]["event"] == "summary"
    assert wrapped["custom_rsp_data"]["content"] == "为您完成如下事项"
    assert wrapped["execution_time"] == 19.891351
    # 不带 error_code
    assert "error_code" not in wrapped


def test_matches_capture_final_answer_chunk_full_text():
    """抓包中 final_answer_chunk 是一次性全量文本。"""
    full_text = (
        "正在总结中：已为您完成如下事项：\n"
        "1. 理财产品推荐（完成）\n"
        "2. 获取产品列表（完成）\n"
        "3. 生成推荐产品若干项（完成）"
    )
    wrapped = wrap_agent_event(
        event_type="final_answer_chunk",
        content=full_text,
        data=None,
        agent_id="agent-1",
        conversation_id="conv-1",
        elapsed=21.8767,
    )
    assert wrapped["custom_rsp_data"]["event"] == "final_answer_chunk"
    assert "\n1. 理财产品推荐（完成）" in wrapped["custom_rsp_data"]["content"]
