# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""RemoteAgentHandler workflow delegation tests."""
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import asyncio

from google.protobuf.json_format import MessageToDict

from tests.framework_parallel._helpers import (
    FakeAsyncStream,
    collect_sub_tasks,
    make_executor,
    make_turn_ctx,
)


def _wfs(*ids):
    return [
        {
            "workflow_id": workflow_id,
            "intent": f"intent-{workflow_id}",
            "task_description": f"task-{workflow_id}",
        }
        for workflow_id in ids
    ]


async def test_multi_delegate_over_limit_rejects_all_without_running():
    executor = make_executor(max_parallel_workflows_per_agent=3)
    handler = executor._test_remote_handler
    called = []

    async def fake_one(workflow, turn_ctx, cancel_event=None):
        called.append(workflow["workflow_id"])
        return {"workflow_id": workflow["workflow_id"], "status": "done"}

    handler._run_one_workflow = fake_one
    capture = {}

    class _Executor:
        async def run_agent(
            self,
            _turn_ctx,
            *,
            query,
            original_body,
            cascade_result,
            run_options=None,
        ):
            capture["cascade"] = cascade_result

    await handler._handle_multi_delegate(
        {"raw_event": {"type": "multi_delegate", "data": {"workflows": _wfs("w1", "w2", "w3", "w4")}}},
        {"turn_ctx": make_turn_ctx(), "executor": _Executor()},
    )

    assert called == []
    results = capture["cascade"]["workflow_results"]
    assert len(results) == 4
    assert all(result["status"] == "failed" for result in results)
    assert all("limit exceeded" in result["error"] for result in results)


async def test_multi_delegate_within_limit_aggregates():
    executor = make_executor(max_parallel_workflows_per_agent=3)
    handler = executor._test_remote_handler

    async def fake_one(workflow, turn_ctx, cancel_event=None):
        return {
            "workflow_id": workflow["workflow_id"],
            "status": "done",
            "result": {"url": f"u-{workflow['workflow_id']}"},
            "error": "",
            "elapsed_ms": 10,
        }

    handler._run_one_workflow = fake_one
    capture = {}

    class _Executor:
        async def run_agent(
            self,
            _turn_ctx,
            *,
            query,
            original_body,
            cascade_result,
            run_options=None,
        ):
            capture["cascade"] = cascade_result

    await handler._handle_multi_delegate(
        {"raw_event": {"type": "multi_delegate", "data": {"workflows": _wfs("wa", "wb")}}},
        {"turn_ctx": make_turn_ctx(), "executor": _Executor()},
    )

    results = capture["cascade"]["workflow_results"]
    assert {result["workflow_id"] for result in results} == {"wa", "wb"}
    assert all(result["status"] == "done" for result in results)


async def test_run_one_workflow_done_path_and_elapsed():
    executor = make_executor()
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(sub_task_path=("A",))

    async def fake_va(_turn_ctx, delegate, path, cancel_event):
        await handler._emit_sub_task(_turn_ctx, path, "workflow", {"event": "message", "data": {"text": "tick"}})
        return {"url": "https://r", "node_type": "End"}

    handler._drive_workflow_va = fake_va

    result = await handler._run_one_workflow(_wfs("wf:a")[0], ctx)

    assert result["workflow_id"] == "wf:a"
    assert result["status"] == "done"
    assert result["result"] == {"url": "https://r", "node_type": "End"}
    assert isinstance(result["elapsed_ms"], int)

    frames = collect_sub_tasks(ctx.event_queue)
    assert frames[0]["node_kind"] == "workflow"
    assert frames[0]["sub_task_path"] == ["A", "wf:a"]
    assert frames[0]["data"] == {"event": "node_start", "intent": "intent-wf:a"}
    assert frames[1]["data"] == {"event": "message", "data": {"text": "tick"}}
    assert frames[-1]["data"]["event"] == "node_end"
    assert frames[-1]["data"]["status"] == "done"


async def test_run_one_workflow_failure_isolated():
    executor = make_executor()
    handler = executor._test_remote_handler
    ctx = make_turn_ctx()

    async def fake_va(_turn_ctx, delegate, path, cancel_event):
        raise RuntimeError("VA failed")

    handler._drive_workflow_va = fake_va

    result = await handler._run_one_workflow(_wfs("wf:c")[0], ctx)

    assert result["status"] == "failed"
    assert "VA failed" in result["error"]
    assert collect_sub_tasks(ctx.event_queue)[-1]["data"]["status"] == "failed"


async def test_run_one_workflow_no_terminal_result_marks_failed():
    """问题 2：VA 流未给出任何终态（final_result is None）→ 判 failed，不静默 done。"""
    executor = make_executor()
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(sub_task_path=("A",))

    async def fake_va(_turn_ctx, delegate, path, cancel_event):
        return None

    handler._drive_workflow_va = fake_va

    result = await handler._run_one_workflow(_wfs("wf:n")[0], ctx, asyncio.Event())

    assert result["status"] == "failed"
    assert result["result"] is None
    assert "未返回终态" in result["error"]
    envs = collect_sub_tasks(ctx.event_queue)
    assert envs[-1]["data"]["event"] == "node_end"
    assert envs[-1]["data"]["status"] == "failed"


async def test_run_one_workflow_timeout():
    executor = make_executor(workflow_timeout_seconds=0.01)
    handler = executor._test_remote_handler
    ctx = make_turn_ctx()

    async def fake_va(_turn_ctx, delegate, path, cancel_event):
        await asyncio.sleep(1)
        return {"url": "late"}

    handler._drive_workflow_va = fake_va

    result = await handler._run_one_workflow(_wfs("wf:t")[0], ctx)

    assert result["status"] == "timeout"
    assert result["error"] == "workflow timeout"
    assert collect_sub_tasks(ctx.event_queue)[-1]["data"] == {
        "event": "node_end",
        "status": "timeout",
        "error": "workflow timeout",
        "elapsed_ms": result["elapsed_ms"],
    }


async def test_run_one_workflow_cancelled():
    executor = make_executor()
    handler = executor._test_remote_handler
    ctx = make_turn_ctx(sub_task_path=("A",))
    cancel = asyncio.Event()

    async def fake_va(_turn_ctx, delegate, path, cancel_event):
        cancel.set()
        return {"url": "r"}

    handler._drive_workflow_va = fake_va

    result = await handler._run_one_workflow(_wfs("wf:x")[0], ctx, cancel)

    assert result["status"] == "cancelled"
    envs = collect_sub_tasks(ctx.event_queue)
    assert envs[-1]["data"]["status"] == "cancelled"


# ════════════════════════════════════════════════════════════════════
# 并行委托 → VA 请求构造：intent 改写对齐 + target 仅用 intent 路由（决策 a/b）
# 这两个用例**不 stub** _drive_workflow_va，直接驱动真实函数体并捕获发往 VA 的请求。
# ════════════════════════════════════════════════════════════════════


def _capture_va_request(executor) -> dict:
    """让真实 _drive_workflow_va 跑起来：捕获发往 VA 的 SendMessageRequest，返回空流。"""
    captured: dict = {}

    def send_message(request):
        captured["request"] = request
        return FakeAsyncStream([])  # 空流 → async for 直接结束，请求已在迭代前构造

    executor._test_va_client.send_message = send_message
    return captured


def _va_data_part(request) -> dict:
    """从 SendMessageRequest 的 DataPart 还原 dict（target/headers/body/params/...）。"""
    for part in request.message.parts:
        if part.WhichOneof("content") == "data":
            return MessageToDict(part.data)
    return {}


def _va_text_part(request) -> str:
    for part in request.message.parts:
        if part.WhichOneof("content") == "text":
            return part.text
    return ""


async def test_drive_workflow_va_rewrites_intent_and_omits_workflow_id():
    """决策 a：推荐入口改写生效，body 入参与 target 的 intent 一致；
    决策 b：target 只用 intent 路由，不含模型生成的局部 workflow_id（wf_path[-1]）。"""
    executor = make_executor()
    handler = executor._test_remote_handler
    captured = _capture_va_request(executor)
    ctx = make_turn_ctx(sub_task_path=("A",))
    delegate = {"type": "delegate", "data": {"intent": "理财推荐", "task_description": "推荐理财产品"}}

    await handler._drive_workflow_va(ctx, delegate, ["A", "wf-1"], asyncio.Event())

    data = _va_data_part(captured["request"])
    # 决策 a：理财推荐 → 理财选品购买；query → 请推荐低风险理财产品（body 与 target 一致）
    assert data["target"]["intent"] == "理财选品购买"
    assert data["body"]["input"]["intent"] == "理财选品购买"
    assert data["body"]["input"]["query"] == "请推荐低风险理财产品"
    assert data["body"]["custom_data"]["inputs"]["intent"] == "理财选品购买"
    assert _va_text_part(captured["request"]) == "请推荐低风险理财产品"
    # 决策 b：target 不含 workflow_id（wf_path[-1]="wf-1" 仅用于节点盖章，不进路由）
    assert data["target"]["type"] == "workflow"
    assert "workflow_id" not in data["target"]


async def test_drive_workflow_va_passthrough_intent_no_rewrite():
    """非改写 intent：body 与 target 用原始 intent；仍不含 workflow_id，
    且 _build_va_message 注入 conversation_id。"""
    executor = make_executor()
    handler = executor._test_remote_handler
    captured = _capture_va_request(executor)
    ctx = make_turn_ctx(conv_id="c", sub_task_path=("A",))
    delegate = {"type": "delegate", "data": {"intent": "转账", "task_description": "给张三转100"}}

    await handler._drive_workflow_va(ctx, delegate, ["A", "wf-9"], asyncio.Event())

    data = _va_data_part(captured["request"])
    assert data["target"] == {"type": "workflow", "intent": "转账", "conversation_id": "c"}
    assert data["body"]["input"]["intent"] == "转账"
    assert data["body"]["input"]["query"] == "给张三转100"
