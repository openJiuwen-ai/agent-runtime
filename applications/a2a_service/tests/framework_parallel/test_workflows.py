# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""子 Agent 侧并行工作流：超限全拒 + gather 聚合 + 单工作流生命周期/超时/取消。

关联用例：LIMIT-03（工作流超限直接全拒、不取前 N）、WF-01/02/05（并行提交、
各自 node_end、path=[entity,wf]）、WF-04（workflow_results 结构）、SSE-02/03、
FAIL-03（单工作流失败隔离）、TMO-02（工作流超时）、CANCEL-03（工作流取消）。
"""
from __future__ import annotations

import asyncio

from common.events import MultiDelegateRequest, WorkflowSpec

from tests.framework_parallel._helpers import (
    collect_sub_tasks,
    make_executor,
    make_turn_ctx,
)


def _wfs(*ids):
    return [WorkflowSpec(workflow_id=i, intent=f"intent-{i}", task_description=f"task-{i}") for i in ids]


# ════════════════════════════════════════════════════════════════════
# LIMIT-03：超限直接全拒绝（保护 VA 后端，不进 gather）
# ════════════════════════════════════════════════════════════════════


async def test_multi_delegate_over_limit_rejects_all_without_running():
    executor = make_executor(max_parallel_workflows_per_agent=3)
    called = []

    async def fake_one(spec, turn_ctx, cancel_event):
        called.append(spec.workflow_id)
        return {"workflow_id": spec.workflow_id, "status": "done"}

    executor._run_one_workflow = fake_one
    ctx = make_turn_ctx()

    cascade = await executor._handle_multi_delegate(
        MultiDelegateRequest(workflows=_wfs("w1", "w2", "w3", "w4")), ctx, asyncio.Event()
    )

    assert called == []  # 不进 gather
    results = cascade["workflow_results"]
    assert len(results) == 4
    assert all(r["status"] == "failed" for r in results)
    assert all("超限" in r["error"] for r in results)
    assert all(r["elapsed_ms"] == 0 for r in results)


async def test_multi_delegate_within_limit_aggregates():
    executor = make_executor(max_parallel_workflows_per_agent=3)

    async def fake_one(spec, turn_ctx, cancel_event):
        return {"workflow_id": spec.workflow_id, "status": "done",
                "result": {"url": f"u-{spec.workflow_id}"}, "error": "", "elapsed_ms": 10}

    executor._run_one_workflow = fake_one
    ctx = make_turn_ctx()

    cascade = await executor._handle_multi_delegate(
        MultiDelegateRequest(workflows=_wfs("wa", "wb")), ctx, asyncio.Event()
    )

    results = cascade["workflow_results"]
    assert {r["workflow_id"] for r in results} == {"wa", "wb"}
    assert all(r["status"] == "done" for r in results)


# ════════════════════════════════════════════════════════════════════
# 单工作流生命周期
# ════════════════════════════════════════════════════════════════════


def _set_fake_va(executor, *, returns=None, raises=None, sleep=None):
    async def fake_va(delegate, turn_ctx, wf_path, cancel_event):
        if sleep is not None:
            await asyncio.sleep(sleep)
        if raises is not None:
            raise raises
        return returns

    executor._drive_workflow_va = fake_va


async def test_run_one_workflow_done_path_and_elapsed():
    executor = make_executor()
    _set_fake_va(executor, returns={"url": "https://r", "node_type": "End"})
    ctx = make_turn_ctx(sub_task_path=("A",))

    result = await executor._run_one_workflow(_wfs("wf:a")[0], ctx, asyncio.Event())

    assert result["workflow_id"] == "wf:a"
    assert result["status"] == "done"
    assert result["result"] == {"url": "https://r", "node_type": "End"}
    assert isinstance(result["elapsed_ms"], int)

    envs = collect_sub_tasks(ctx.event_queue)
    # node_start：workflow 节点，path=[entity, workflow_id]，带 intent
    assert envs[0]["node_kind"] == "workflow"
    assert envs[0]["sub_task_path"] == ["A", "wf:a"]
    assert envs[0]["data"] == {"event": "node_start", "intent": "intent-wf:a"}
    # node_end done，result 内含 elapsed_ms
    assert envs[-1]["data"]["event"] == "node_end"
    assert envs[-1]["data"]["status"] == "done"
    assert "elapsed_ms" in envs[-1]["data"]["result"]


async def test_run_one_workflow_failure_isolated():
    executor = make_executor()
    _set_fake_va(executor, raises=RuntimeError("VA报错"))
    ctx = make_turn_ctx(sub_task_path=("A",))

    result = await executor._run_one_workflow(_wfs("wf:c")[0], ctx, asyncio.Event())

    assert result["status"] == "failed"
    assert "VA报错" in result["error"]
    envs = collect_sub_tasks(ctx.event_queue)
    assert envs[-1]["data"]["status"] == "failed"
    assert envs[-1]["sub_task_path"] == ["A", "wf:c"]


async def test_run_one_workflow_timeout():
    executor = make_executor(workflow_timeout_seconds=0.05)
    _set_fake_va(executor, returns={"url": "late"}, sleep=1)
    ctx = make_turn_ctx(sub_task_path=("A",))

    result = await executor._run_one_workflow(_wfs("wf:t")[0], ctx, asyncio.Event())

    assert result["status"] == "timeout"
    assert "超时" in result["error"]
    envs = collect_sub_tasks(ctx.event_queue)
    assert envs[-1]["data"] == {"event": "node_end", "status": "timeout", "error": "工作流超时"}


async def test_run_one_workflow_cancelled():
    executor = make_executor()
    _set_fake_va(executor, returns={"url": "r"})
    ctx = make_turn_ctx(sub_task_path=("A",))
    cancel = asyncio.Event()
    cancel.set()  # VA 返回后检测到取消

    result = await executor._run_one_workflow(_wfs("wf:x")[0], ctx, cancel)

    assert result["status"] == "cancelled"
    envs = collect_sub_tasks(ctx.event_queue)
    assert envs[-1]["data"]["status"] == "cancelled"
