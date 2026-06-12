# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""终态门控回归测试：final_answer_end → COMPLETED 只对顶层主 Agent 生效，子 Agent 路径降级。

背景（见 ref_deploy_a2a_troubleshooting 第 4 条）：A2A server 一旦在流里遇到 COMPLETED
终态即关流。规划型 Agent 在工具调用之间会发多段 final_answer_end（含首段开场白）。
若子 Agent 的中途 final_answer_end 也映射成 COMPLETED，其 A2A server 会在首段开场白处
提前关流，后续 cascade（call_versatile / call_multiversatile）的真实结果回传不到父侧
（父侧 _drive_sub_agent 提前 return → 主侧拿不到真实报告而编造）。

修复按 ``turn_ctx.sub_task_path`` 网关化：
  - 顶层主 Agent（sub_task_path 为空）：维持 final_answer_end → COMPLETED，前端报文序列不变；
    且 Executor 不在自然结束处补发额外 COMPLETED。
  - 子 Agent（sub_task_path 非空）：中途 final_answer_end 降级为非终态 artifact；
    Executor 在本轮自然结束处补发**唯一一次**终态 COMPLETED，content 取最后一段 final_answer。
"""
from __future__ import annotations

import pytest

from common.events import FinalAnswerChunkEvent, FinalAnswerEndEvent, ThinkChunkEvent

from tests.framework_parallel._helpers import (
    artifact_data,
    drain_queue,
    make_executor,
    make_turn_ctx,
)
from a2a.types.a2a_pb2 import (
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    TASK_STATE_COMPLETED,
)


def _completed_text(status_event) -> str:
    msg = status_event.status.message
    if not msg:
        return ""
    for part in msg.parts:
        if part.WhichOneof("content") == "text":
            return part.text
    return ""


def _completed_events(events: list) -> list:
    return [
        e for e in events
        if isinstance(e, TaskStatusUpdateEvent) and e.status.state == TASK_STATE_COMPLETED
    ]


def _final_answer_artifacts(events: list) -> list[dict]:
    """data.type == final_answer_end 的 artifact（即降级透传的 final_answer_end）。"""
    out: list[dict] = []
    for ev in events:
        if isinstance(ev, TaskArtifactUpdateEvent):
            d = artifact_data(ev)
            if d and d.get("type") == "final_answer_end":
                out.append(d)
    return out


def _patch_agent_stream(monkeypatch, events: list) -> None:
    async def fake_agent_stream(**_kwargs):
        for ev in events:
            yield ev

    monkeypatch.setattr("orchestrator.executor.agent_stream", fake_agent_stream)


# ── 一轮 run 内多段 final_answer_end：首段开场白 + 末段真正报告 ──────────────────
def _agent_events() -> list:
    return [
        FinalAnswerEndEvent(content="好的，我先读取 SKILL.md，随后执行步骤1"),  # 开场白（中途）
        ThinkChunkEvent(content="分析中"),
        FinalAnswerEndEvent(content="最终信贷分析报告正文"),                     # 真正的最终回答
    ]


@pytest.mark.asyncio
async def test_sub_agent_final_answer_end_not_terminal_single_completed_at_end(monkeypatch):
    """子 Agent（sub_task_path 非空）：中途 final_answer_end 全降级 artifact，末尾补唯一一次 COMPLETED。"""
    _patch_agent_stream(monkeypatch, _agent_events())
    executor = make_executor()
    turn_ctx = make_turn_ctx(sub_task_path=("entity_001",))

    await executor.run_agent(turn_ctx, query="对小米进行信贷分析", original_body={}, cascade_result=None)

    events = drain_queue(turn_ctx.event_queue)  # drain_queue 是破坏性的，只抽一次
    completed = _completed_events(events)
    # 关键 1：整轮只有一次 COMPLETED（不会在首段开场白处提前判终态）
    assert len(completed) == 1, f"子 Agent 应只在末尾补发一次 COMPLETED，实际 {len(completed)}"
    # 关键 2：这次 COMPLETED 取的是最后一段 final_answer（真正报告），不是开场白
    assert _completed_text(completed[0]) == "最终信贷分析报告正文"
    # 关键 3：两段 final_answer_end 都以非终态 artifact 透传（前端经 sub_task 仍可见 final_answer_end）
    fa_artifacts = _final_answer_artifacts(events)
    assert len(fa_artifacts) == 2
    assert [d["content"] for d in fa_artifacts] == [
        "好的，我先读取 SKILL.md，随后执行步骤1",
        "最终信贷分析报告正文",
    ]
    # 关键 4：COMPLETED 是整轮最后一个事件（终态收尾）
    assert isinstance(events[-1], TaskStatusUpdateEvent)
    assert events[-1].status.state == TASK_STATE_COMPLETED


# ── 真实 emission：全量文本走 final_answer_chunk，final_answer_end.content 为空 ──────
def _agent_events_realistic() -> list:
    """对齐真实子 Agent 报文（见 sub_entity_001.log）：权威全量文本在 FinalAnswerChunkEvent，
    FinalAnswerEndEvent.content 恒为空串。若 Executor 只读 end.content，cascade content 即为空，
    父 Agent 拿到 sub_agent_results[*].content="" 而编造（ref_deploy_a2a_troubleshooting #7）。"""
    return [
        FinalAnswerChunkEvent(content="现在执行步骤1"),   # 中途全量帧（开场白阶段）
        FinalAnswerEndEvent(content=""),                  # 结束标记，空
        ThinkChunkEvent(content="分析中"),
        FinalAnswerChunkEvent(content="最终信贷分析报告正文"),  # 真正的最终回答（全量）
        FinalAnswerEndEvent(content=""),                  # 结束标记，空
    ]


@pytest.mark.asyncio
async def test_sub_agent_cascade_content_from_chunk_when_end_empty(monkeypatch):
    """子 Agent：final_answer_end.content 为空时，补发的 COMPLETED 必须取 final_answer_chunk 全量文本。

    回归 ref_deploy_a2a_troubleshooting #7：只读 final_answer_end.content 会让 cascade content 恒空，
    主 Agent 拿不到子 Agent 真实报告而编造。"""
    _patch_agent_stream(monkeypatch, _agent_events_realistic())
    executor = make_executor()
    turn_ctx = make_turn_ctx(sub_task_path=("entity_001",))

    await executor.run_agent(turn_ctx, query="对小米进行信贷分析", original_body={}, cascade_result=None)

    events = drain_queue(turn_ctx.event_queue)
    completed = _completed_events(events)
    assert len(completed) == 1, f"子 Agent 应只在末尾补发一次 COMPLETED，实际 {len(completed)}"
    # 关键：COMPLETED 携带的是 chunk 的全量报告，而非空的 end.content
    assert _completed_text(completed[0]) == "最终信贷分析报告正文", (
        f"cascade content 应取 final_answer_chunk 全量文本，实际 {_completed_text(completed[0])!r}"
    )


@pytest.mark.asyncio
async def test_top_level_final_answer_end_maps_to_completed_unchanged(monkeypatch):
    """顶层主 Agent（sub_task_path 为空）：维持 final_answer_end → COMPLETED，且不补发额外终态。"""
    _patch_agent_stream(monkeypatch, _agent_events())
    executor = make_executor()
    turn_ctx = make_turn_ctx(sub_task_path=())

    await executor.run_agent(turn_ctx, query="对小米进行信贷分析", original_body={}, cascade_result=None)

    events = drain_queue(turn_ctx.event_queue)
    completed = _completed_events(events)
    # 顶层：每段 final_answer_end 各产一个 COMPLETED（历史行为），共 2 个；Executor 不再额外补发
    assert len(completed) == 2, f"顶层应保持每段 final_answer_end → COMPLETED（2 个），实际 {len(completed)}"
    assert [_completed_text(c) for c in completed] == [
        "好的，我先读取 SKILL.md，随后执行步骤1",
        "最终信贷分析报告正文",
    ]
    # 顶层不应把 final_answer_end 降级成 artifact（否则前端报文序列变化）
    assert _final_answer_artifacts(events) == []
