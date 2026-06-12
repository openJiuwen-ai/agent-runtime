# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""北向序列化：sub_task 信封 round-trip + 向后兼容 + extract_content oneof 守卫。

链路：SubTaskEvent --agent_adapter--> TaskArtifactUpdateEvent
      --user_router._extract_event_meta--> meta
      --user_router._serialize_event--> 北向 JSON。

关联用例：SSE-01/02/03（agent/workflow/lifecycle 帧）、SSE-04（按 path 建树）、
SSE-08（子帧 failed 时外层 success=true）、COMPAT-01/02（非 sub_task 逐字段不变）、
CTX-07 / TECH §3.2 实现注 2（extract_content oneof）。
"""
from __future__ import annotations

import json
import time

from common.events import SubTaskEvent, ThinkChunkEvent
from orchestrator.agent_adapter import agent_event_to_a2a
from orchestrator.executor import extract_content
from orchestrator.user_router import (
    _extract_event_meta,
    _extract_inner_by_kind,
    _serialize_event,
)

from tests.framework_parallel._helpers import (
    artifact_event,
    status_completed_data,
    status_completed_empty,
    status_completed_text,
)


def _serialize_sub_task(ev: SubTaskEvent) -> dict:
    """走真实链路把 SubTaskEvent 渲染为北向 JSON dict。"""
    a2a = agent_event_to_a2a(ev, "task-1", "conv-1")
    out = _serialize_event(a2a, agent_id="ag", conversation_id="conv-1", start_time=time.monotonic())
    assert out is not None
    return json.loads(out)


# ── _extract_inner_by_kind 三类内层 ─────────────────────────────────────────


def test_inner_kind_agent_frame():
    meta = _extract_inner_by_kind({"type": "think_chunk", "content": "hi"})
    assert meta["kind"] == "agent"
    assert meta["type"] == "think_chunk"
    assert meta["content"] == "hi"


def test_inner_kind_lifecycle_node_start_and_end():
    assert _extract_inner_by_kind({"event": "node_start", "entity_name": "企业A"})["kind"] == "lifecycle"
    assert _extract_inner_by_kind({"event": "node_end", "status": "done"})["kind"] == "lifecycle"


def test_inner_kind_workflow_message():
    meta = _extract_inner_by_kind({"event": "message", "data": {"node_type": "LLM", "text": "x"}})
    assert meta["kind"] == "workflow"
    assert meta["type"] == "message"
    assert meta["data"] == {"node_type": "LLM", "text": "x"}


# ── _extract_event_meta sub_task 分支 ───────────────────────────────────────


def test_extract_meta_sub_task_branch_lifts_path_and_kind():
    a2a = agent_event_to_a2a(
        SubTaskEvent(sub_task_path=["A"], node_kind="agent",
                     data={"event": "node_start", "entity_name": "企业A"}),
        "t", "c",
    )
    meta = _extract_event_meta(a2a)
    assert meta["kind"] == "sub_task"
    assert meta["sub_task_path"] == ["A"]
    assert meta["node_kind"] == "agent"
    assert meta["inner"]["kind"] == "lifecycle"


# ── round-trip：agent / workflow / lifecycle ───────────────────────────────


def test_roundtrip_agent_report_frame():
    wrapped = _serialize_sub_task(
        SubTaskEvent(sub_task_path=["A"], node_kind="agent",
                     data={"type": "think_chunk", "content": "正在分析A"})
    )
    crd = wrapped["custom_rsp_data"]
    assert crd["event"] == "sub_task"
    assert crd["sub_task_path"] == ["A"]
    assert crd["node_kind"] == "agent"
    # inner 渲染为标准 agent custom_rsp_data
    assert crd["data"]["event"] == "think_chunk"
    assert crd["data"]["content"] == "正在分析A"


def test_roundtrip_node_start_lifecycle_passthrough():
    wrapped = _serialize_sub_task(
        SubTaskEvent(sub_task_path=["B"], node_kind="agent",
                     data={"event": "node_start", "entity_name": "企业B"})
    )
    # 生命周期帧原样透传到 data
    assert wrapped["custom_rsp_data"]["data"] == {"event": "node_start", "entity_name": "企业B"}


def test_roundtrip_workflow_message_frame():
    wrapped = _serialize_sub_task(
        SubTaskEvent(sub_task_path=["A", "wf:a"], node_kind="workflow",
                     data={"event": "message", "data": {"node_type": "LLM", "text": "解析财报"}})
    )
    crd = wrapped["custom_rsp_data"]
    assert crd["node_kind"] == "workflow"
    assert crd["sub_task_path"] == ["A", "wf:a"]
    assert crd["data"] == {"event": "message", "data": {"node_type": "LLM", "text": "解析财报"}}


def test_roundtrip_deep_path_preserved_for_tree_building():
    """SSE-04：任意深度 path 原样保留，前端可按前缀建树。"""
    wrapped = _serialize_sub_task(
        SubTaskEvent(sub_task_path=["A", "X", "wf:z"], node_kind="workflow",
                     data={"event": "message", "data": {"text": "深层"}})
    )
    assert wrapped["custom_rsp_data"]["sub_task_path"] == ["A", "X", "wf:z"]


# ── SSE-08：子帧 node_end(failed) 外层 success 仍为 true ─────────────────────


def test_sub_task_failed_inner_keeps_outer_success_true():
    wrapped = _serialize_sub_task(
        SubTaskEvent(sub_task_path=["A", "wf:c"], node_kind="workflow",
                     data={"event": "node_end", "status": "failed", "error": "VA接口超时"})
    )
    assert wrapped["success"] is True  # 传输成功；错误体现在 data.status
    assert wrapped["custom_rsp_data"]["data"]["status"] == "failed"


# ── COMPAT：非 sub_task 帧走标准 agent / workflow 路径，不含 sub_task ─────────


def test_compat_plain_agent_frame_serialization_unchanged():
    a2a = agent_event_to_a2a(ThinkChunkEvent(content="主Agent推理"), "t", "c")
    wrapped = json.loads(
        _serialize_event(a2a, agent_id="ag", conversation_id="c", start_time=time.monotonic())
    )
    crd = wrapped["custom_rsp_data"]
    assert crd["event"] == "think_chunk"
    assert crd["content"] == "主Agent推理"
    assert "sub_task_path" not in crd  # 非并行路径绝不出现 sub_task 字段


def test_compat_plain_workflow_message_frame_unchanged():
    a2a = artifact_event({"event": "message", "data": {"node_type": "QA", "text": "单工作流"}})
    wrapped = json.loads(
        _serialize_event(a2a, agent_id="ag", conversation_id="c", start_time=time.monotonic())
    )
    crd = wrapped["custom_rsp_data"]
    assert crd["event"] == "message"
    assert crd["data"] == {"node_type": "QA", "text": "单工作流"}
    assert "sub_task_path" not in crd


def test_workflow_end_frame_no_longer_suppressed():
    """sidecar VA 不再发 ``event:"end"`` 帧（完成走 TaskStatusUpdateEvent(COMPLETED)）；
    旧的 end 抑制已随同事重构移除，若仍出现这类帧则按普通 workflow 事件转发、不再吞掉。"""
    a2a = artifact_event({"event": "end", "data": {}})
    out = _serialize_event(a2a, agent_id="ag", conversation_id="c", start_time=time.monotonic())
    assert out is not None
    assert '"event": "end"' in out


# ── extract_content oneof 守卫（CTX-07）─────────────────────────────────────


def test_extract_content_text_part():
    assert extract_content(status_completed_text("最终回答")) == "最终回答"


def test_extract_content_data_part_serialized():
    out = extract_content(status_completed_data({"entity_id": "A", "url": "https://x"}))
    assert json.loads(out) == {"entity_id": "A", "url": "https://x"}


def test_extract_content_empty_message():
    assert extract_content(status_completed_empty()) == ""
