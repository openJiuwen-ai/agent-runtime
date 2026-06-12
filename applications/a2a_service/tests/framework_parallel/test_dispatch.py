# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""主 Agent 侧并行子 Agent 调度：双闸门 + 故障隔离 + 单子 Agent 生命周期 + 超时。

关联用例：LIMIT-01/02（并发截断取前 N）、深度门控（max_call_depth）、
DISP-01（拆解并行）、STATE-01/02/04（终态/部分成功/状态流转）、
FAIL-01（单子异常隔离）、TMO-01（子 Agent 超时）、CTX-03（child_task_id 捕获）、
P-009（已盖章深层帧透传）。
"""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import MagicMock

from common.events import SubAgentDispatchRequest, SubAgentResult, SubAgentSpec

from tests.framework_parallel._helpers import (
    collect_sub_tasks,
    make_executor,
    make_turn_ctx,
)


def _specs(*ids):
    return [SubAgentSpec(entity_id=i, entity_name=f"企业{i}", query=f"分析{i}") for i in ids]


# ════════════════════════════════════════════════════════════════════
# 闸门①：递归深度门控
# ════════════════════════════════════════════════════════════════════


async def test_depth_gate_rejects_all_when_exceeding_max_depth():
    executor = make_executor(sub_agent_client=MagicMock(), max_call_depth=3)
    # self_depth = len(sub_task_path) = 3 → 3+1 > 3，全拒
    ctx = make_turn_ctx(sub_task_path=("A", "B", "C"))
    results, skipped = await executor._handle_sub_agent_dispatch(
        SubAgentDispatchRequest(specs=_specs("X", "Y")), ctx, {}
    )
    assert results == []
    assert [s["reason"] for s in skipped] == ["max_call_depth", "max_call_depth"]
    assert {s["entity_id"] for s in skipped} == {"X", "Y"}


# ════════════════════════════════════════════════════════════════════
# 闸门②：并发截断（取前 N），其余 skipped(concurrency_limit)
# ════════════════════════════════════════════════════════════════════


async def test_concurrency_truncation_keeps_first_n():
    # sub_agent_client=None → 派发降级 failed，但截断/skipped 逻辑先于此生效，可独立验证
    executor = make_executor(sub_agent_client=None, max_concurrent_sub_agents=3)
    ctx = make_turn_ctx()
    results, skipped = await executor._handle_sub_agent_dispatch(
        SubAgentDispatchRequest(specs=_specs("A", "B", "C", "D", "E")), ctx, {}
    )
    # 前 3 个被派发（此处降级 failed），后 2 个 skipped
    assert [r.entity_id for r in results] == ["A", "B", "C"]
    assert [s["entity_id"] for s in skipped] == ["D", "E"]
    assert {s["reason"] for s in skipped} == {"concurrency_limit"}


async def test_exactly_at_limit_no_skip():
    executor = make_executor(sub_agent_client=None, max_concurrent_sub_agents=3)
    ctx = make_turn_ctx()
    results, skipped = await executor._handle_sub_agent_dispatch(
        SubAgentDispatchRequest(specs=_specs("A", "B", "C")), ctx, {}
    )
    assert len(results) == 3
    assert skipped == []


async def test_no_sub_agent_client_degrades_to_failed():
    executor = make_executor(sub_agent_client=None)
    ctx = make_turn_ctx()
    results, _ = await executor._handle_sub_agent_dispatch(
        SubAgentDispatchRequest(specs=_specs("A")), ctx, {}
    )
    assert results[0].status == "failed"
    assert "未启用" in results[0].error


# ════════════════════════════════════════════════════════════════════
# gather 聚合 + 故障隔离（单子异常 → failed，不影响其余）
# ════════════════════════════════════════════════════════════════════


async def test_gather_isolates_single_failure():
    executor = make_executor(sub_agent_client=MagicMock())

    async def fake_with_timeout(spec, turn_ctx, cancel_event):
        if spec.entity_id == "B":
            raise RuntimeError("boom-B")
        return SubAgentResult(entity_id=spec.entity_id, status="done", content=spec.entity_id)

    executor._run_sub_agent_with_timeout = fake_with_timeout
    ctx = make_turn_ctx(conv_id="conv-x")

    results, skipped = await executor._handle_sub_agent_dispatch(
        SubAgentDispatchRequest(specs=_specs("A", "B", "C")), ctx, {}
    )

    by_id = {r.entity_id: r for r in results}
    assert by_id["A"].status == "done"
    assert by_id["C"].status == "done"
    assert by_id["B"].status == "failed"  # 异常被转为 failed
    assert "boom-B" in by_id["B"].error
    # 令牌与 child_task_ids 在 finally 中清理
    assert "conv-x" not in executor._cancel_tokens
    assert "conv-x" not in executor._child_task_ids


# ════════════════════════════════════════════════════════════════════
# 单子 Agent 生命周期：node_start → report → node_end + 状态
# ════════════════════════════════════════════════════════════════════


def _set_fake_drive(executor, *, frames=None, raises=None):
    async def fake_drive(spec, sub_conv_id, child_path, turn_ctx, cancel_event):
        for f in (frames or []):
            yield f
        if raises is not None:
            raise raises

    executor._drive_sub_agent = fake_drive


async def test_run_sub_agent_done_emits_lifecycle_and_captures_child_id():
    executor = make_executor(sub_agent_client=MagicMock())
    _set_fake_drive(executor, frames=[
        {"type": "__task_created__", "task_id": "child-1"},
        {"type": "think_chunk", "content": "分析中"},
        {"type": "__completed__", "content": "实体A回答"},
    ])
    ctx = make_turn_ctx(conv_id="conv-1")

    result = await executor._run_sub_agent(
        _specs("A")[0], ctx, asyncio.Event(), ("A",)
    )

    assert result.status == "done"
    assert result.content == "实体A回答"
    assert result.child_task_id == "child-1"
    assert executor._child_task_ids["conv-1"]["A"]["task_id"] == "child-1"

    envs = collect_sub_tasks(ctx.event_queue)
    events = [(e["node_kind"], e["data"].get("event"), e["sub_task_path"]) for e in envs]
    assert events[0] == ("agent", "node_start", ["A"])
    # report：原始 agent 帧（保留 type 键）
    assert envs[1]["data"] == {"type": "think_chunk", "content": "分析中"}
    assert events[-1] == ("agent", "node_end", ["A"])
    assert envs[-1]["data"]["status"] == "done"
    assert envs[-1]["data"]["content"] == "实体A回答"


async def test_run_sub_agent_failure_marks_failed():
    executor = make_executor(sub_agent_client=MagicMock())
    _set_fake_drive(
        executor,
        frames=[{"type": "__task_created__", "task_id": "child-9"}],
        raises=RuntimeError("接口报错"),
    )
    ctx = make_turn_ctx()

    result = await executor._run_sub_agent(_specs("A")[0], ctx, asyncio.Event(), ("A",))

    assert result.status == "failed"
    assert "接口报错" in result.error
    assert result.child_task_id == "child-9"
    envs = collect_sub_tasks(ctx.event_queue)
    assert envs[-1]["data"] == {"event": "node_end", "status": "failed", "error": "接口报错"}


async def test_run_sub_agent_cancelled_when_token_set():
    executor = make_executor(sub_agent_client=MagicMock())
    _set_fake_drive(executor, frames=[])  # 无帧，循环后检测 cancel
    ctx = make_turn_ctx()
    cancel = asyncio.Event()
    cancel.set()

    result = await executor._run_sub_agent(_specs("A")[0], ctx, cancel, ("A",))

    assert result.status == "cancelled"
    envs = collect_sub_tasks(ctx.event_queue)
    assert envs[-1]["data"]["status"] == "cancelled"


async def test_run_sub_agent_passes_through_already_stamped_deep_frame():
    """P-009：更深层（子 Agent 再派）已盖章帧透传，不重盖为本节点 path。"""
    executor = make_executor(sub_agent_client=MagicMock())
    _set_fake_drive(executor, frames=[
        {"type": "__task_created__", "task_id": "child-1"},
        {"type": "sub_task", "sub_task_path": ["A", "X"], "node_kind": "agent",
         "data": {"type": "think_chunk", "content": "孙子节点"}},
        {"type": "__completed__", "content": "done"},
    ])
    ctx = make_turn_ctx()

    await executor._run_sub_agent(_specs("A")[0], ctx, asyncio.Event(), ("A",))

    envs = collect_sub_tasks(ctx.event_queue)
    deep = [e for e in envs if e["sub_task_path"] == ["A", "X"]]
    assert len(deep) == 1
    assert deep[0]["data"] == {"type": "think_chunk", "content": "孙子节点"}


# ════════════════════════════════════════════════════════════════════
# TMO-01：子 Agent 超时 → node_end(timeout) + SubAgentResult(timeout)
# ════════════════════════════════════════════════════════════════════


async def test_sub_agent_timeout():
    executor = make_executor(sub_agent_client=MagicMock(), sub_agent_timeout_seconds=0.05)

    async def slow(spec, turn_ctx, cancel_event, child_path):
        await asyncio.sleep(1)
        return SubAgentResult(entity_id=spec.entity_id, status="done")

    executor._run_sub_agent = slow
    ctx = make_turn_ctx()

    result = await executor._run_sub_agent_with_timeout(_specs("A")[0], ctx, asyncio.Event())

    assert result.status == "timeout"
    assert "超时" in result.error
    envs = collect_sub_tasks(ctx.event_queue)
    assert envs[-1]["node_kind"] == "agent"
    assert envs[-1]["data"] == {"event": "node_end", "status": "timeout", "error": "子Agent执行超时"}
