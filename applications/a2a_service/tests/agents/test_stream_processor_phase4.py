# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
_StreamProcessor Phase 4 行为集成测试（agent-store-zhl/community/EDPAgent/agent.py
和 agent-runtime/applications/a2a_service/agents/EDPAgent/agent.py 保持同步）。

验证：
  1. todolist_item 的 content 包含 HTML <br/> 格式
  2. llm_output 流式发 SummaryEvent（不是 FinalAnswerChunkEvent）
  3. answer 事件结束时，补一条全量 FinalAnswerChunkEvent + FinalAnswerEndEvent
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.EDPAgent.agent import _StreamProcessor
from common.events import (
    FinalAnswerChunkEvent,
    FinalAnswerEndEvent,
    FinalAnswerStartEvent,
    SummaryEvent,
    ThinkChunkEvent,
    ThinkEndEvent,
    ThinkStartEvent,
    TodoListEndEvent,
    TodoListItemEvent,
    TodoListStartEvent,
)


@dataclass
class _FakeRawEvent:
    """模拟 Runner 原始事件。"""
    type: str
    payload: dict


def _feed_reasoning(proc: _StreamProcessor, text: str) -> list:
    """把一段文本作为 llm_reasoning 喂进去并 flush。"""
    events = []
    events.extend(proc.process(_FakeRawEvent(type="llm_reasoning", payload={"content": text})))
    events.extend(proc.finalize())
    return events


# ════════════════════════════════════════════════════════════════════
# 4.1 todolist_item 的 content 带 HTML <br/>
# ════════════════════════════════════════════════════════════════════


def test_todolist_item_content_contains_html_br():
    """LLM 产出 todolist JSON → 每个 item 的 content 应是 HTML 片段。"""
    proc = _StreamProcessor()
    reasoning_text = (
        "规划如下：\n\n"
        "```todolist\n"
        "[\n"
        '  {"id": 1, "title": "推荐理财产品", "status": "pending"},\n'
        '  {"id": 2, "title": "确认购买金额", "status": "pending"}\n'
        "]\n"
        "```\n"
    )
    events = _feed_reasoning(proc, reasoning_text)

    items = [e for e in events if isinstance(e, TodoListItemEvent)]
    assert len(items) == 2
    for item in items:
        assert item.content.endswith("<br/>")
        # 格式：{id}.{title}（{中文状态}）<br/>
        assert f"{item.id}." in item.content
        assert item.title in item.content
        assert "（待执行）" in item.content


def test_todolist_item_status_cn_mapping():
    """各 status 的中文映射都对。"""
    proc = _StreamProcessor()
    reasoning_text = (
        "```todolist\n"
        "[\n"
        '  {"id": 1, "title": "A", "status": "pending"},\n'
        '  {"id": 2, "title": "B", "status": "in_progress"},\n'
        '  {"id": 3, "title": "C", "status": "done"},\n'
        '  {"id": 4, "title": "D", "status": "failed"}\n'
        "]\n"
        "```\n"
    )
    events = _feed_reasoning(proc, reasoning_text)
    items = [e for e in events if isinstance(e, TodoListItemEvent)]
    contents = {item.id: item.content for item in items}
    assert "（待执行）" in contents[1]
    assert "（执行中）" in contents[2]
    assert "（完成）" in contents[3]
    assert "（失败）" in contents[4]


# ════════════════════════════════════════════════════════════════════
# 4.2 llm_output → SummaryEvent 流式；answer → FinalAnswerChunkEvent 全量
# ════════════════════════════════════════════════════════════════════


def test_llm_output_emits_summary_events_not_chunk():
    """流式 llm_output 片段应该产 SummaryEvent，不是 FinalAnswerChunkEvent。"""
    proc = _StreamProcessor()
    pieces = ["已", "为您完成", "如下事项"]

    collected = []
    for p in pieces:
        collected.extend(
            proc.process(_FakeRawEvent(type="llm_output", payload={"content": p}))
        )

    # 首帧应是 FinalAnswerStartEvent
    assert isinstance(collected[0], FinalAnswerStartEvent)

    # 后续每个流式片段都是 SummaryEvent
    summaries = [e for e in collected if isinstance(e, SummaryEvent)]
    chunks = [e for e in collected if isinstance(e, FinalAnswerChunkEvent)]

    assert len(summaries) == 3
    assert [s.content for s in summaries] == pieces
    # 流式阶段不应出现 FinalAnswerChunkEvent
    assert chunks == []


def test_answer_event_emits_final_chunk_plus_end():
    """answer 事件到达时补发全量 FinalAnswerChunkEvent + FinalAnswerEndEvent。"""
    proc = _StreamProcessor()
    pieces = ["已", "为您完成", "如下事项"]
    for p in pieces:
        proc.process(_FakeRawEvent(type="llm_output", payload={"content": p}))

    # answer 事件触发 end
    events = proc.process(_FakeRawEvent(type="answer", payload={"content": ""}))

    chunks = [e for e in events if isinstance(e, FinalAnswerChunkEvent)]
    ends = [e for e in events if isinstance(e, FinalAnswerEndEvent)]
    assert len(chunks) == 1
    assert len(ends) == 1
    # 全量文本 = 流式片段拼接
    assert chunks[0].content == "已为您完成如下事项"
    assert ends[0].content == "已为您完成如下事项"


def test_answer_event_without_prior_output_still_emits_full_triple():
    """answer 事件到达但此前无 llm_output → 仍补 start + chunk + end。"""
    proc = _StreamProcessor()
    events = proc.process(
        _FakeRawEvent(type="answer", payload={"content": "直接答案"})
    )
    starts = [e for e in events if isinstance(e, FinalAnswerStartEvent)]
    chunks = [e for e in events if isinstance(e, FinalAnswerChunkEvent)]
    ends = [e for e in events if isinstance(e, FinalAnswerEndEvent)]
    assert len(starts) == 1
    assert len(chunks) == 1
    assert len(ends) == 1
    assert chunks[0].content == "直接答案"


def test_flush_answer_if_interrupted_still_emits_chunk():
    """answering 状态被打断（finalize 时）也要补全量 chunk + end。"""
    proc = _StreamProcessor()
    proc.process(_FakeRawEvent(type="llm_output", payload={"content": "部分"}))
    proc.process(_FakeRawEvent(type="llm_output", payload={"content": "内容"}))

    flushed = proc.finalize()
    chunks = [e for e in flushed if isinstance(e, FinalAnswerChunkEvent)]
    ends = [e for e in flushed if isinstance(e, FinalAnswerEndEvent)]
    assert len(chunks) == 1
    assert chunks[0].content == "部分内容"
    assert len(ends) == 1
