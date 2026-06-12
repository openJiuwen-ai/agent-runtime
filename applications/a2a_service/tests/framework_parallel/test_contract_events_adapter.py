# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""数据契约 + agent_adapter 映射（不依赖任何 Agent / 测试桩）。

覆盖：events.py 新结构的字段契约、EVENT_TYPE_MAP、AgentEvent → A2A 映射
（派发请求吞掉、SubTaskEvent 盖章、既有事件向后兼容不变）。

关联用例：TECH P0-1 事件信封不丢字段、WF-04/P1-5 WorkflowSpec 绑定、
SSE-08 派发请求不出 SSE、COMPAT-01/02 向后兼容。
"""
from __future__ import annotations

from google.protobuf.json_format import MessageToDict

from a2a.types.a2a_pb2 import TaskArtifactUpdateEvent
from common.events import (
    EVENT_TYPE_MAP,
    MultiDelegateRequest,
    SubAgentDispatchRequest,
    SubAgentResult,
    SubAgentSpec,
    SubTaskEvent,
    ThinkChunkEvent,
    WorkflowSpec,
)
from orchestrator.agent_adapter import agent_event_to_a2a

from tests.framework_parallel._helpers import artifact_data


# ── WorkflowSpec → VA 字段映射（workflow_id 不传给 VA）───────────────────────


def test_workflowspec_va_fields_drops_workflow_id_and_maps_target():
    spec = WorkflowSpec(workflow_id="wf:a", intent="财报分析", task_description="分析A财报",
                        target_agent="agent-x")
    fields = spec.va_fields()
    assert fields == {"intent": "财报分析", "task_description": "分析A财报", "target_agent": "agent-x"}
    assert "workflow_id" not in fields  # workflow_id 仅作 sub_task_path 段，不进 VA


def test_workflowspec_empty_target_agent_becomes_none():
    spec = WorkflowSpec(workflow_id="wf:a", intent="i", task_description="t")
    assert spec.va_fields()["target_agent"] is None


# ── SubTaskEvent / SubAgentResult 字段契约 ──────────────────────────────────


def test_subtaskevent_defaults_and_dump():
    ev = SubTaskEvent(sub_task_path=["A"], node_kind="agent")
    assert ev.type == "sub_task"
    assert ev.data == {}
    dumped = ev.model_dump()
    assert dumped == {"type": "sub_task", "sub_task_path": ["A"], "node_kind": "agent", "data": {}}


def test_subagentresult_defaults_and_frozen():
    r = SubAgentResult(entity_id="A", status="done")
    assert (r.content, r.error, r.child_task_id) == ("", "", "")
    # frozen dataclass：不可改
    try:
        r.status = "failed"  # type: ignore[misc]
    except Exception as exc:
        assert "frozen" in type(exc).__name__.lower() or "cannot assign" in str(exc).lower()
    else:
        raise AssertionError("SubAgentResult 应为 frozen")


def test_event_type_map_has_sub_task():
    assert EVENT_TYPE_MAP["sub_task"] is SubTaskEvent


# ── agent_adapter：派发请求不出 SSE ─────────────────────────────────────────


def test_dispatch_requests_map_to_none():
    assert agent_event_to_a2a(SubAgentDispatchRequest(specs=[]), "t", "c") is None
    assert agent_event_to_a2a(MultiDelegateRequest(workflows=[]), "t", "c") is None


def test_subagentspec_is_plain_dataclass():
    s = SubAgentSpec(entity_id="A", entity_name="企业A", query="分析A")
    assert (s.entity_id, s.entity_name, s.query) == ("A", "企业A", "分析A")


# ── agent_adapter：SubTaskEvent → artifact，data 带 type="sub_task" ──────────


def test_subtaskevent_maps_to_artifact_with_stamp():
    ev = SubTaskEvent(
        sub_task_path=["A", "wf:a"], node_kind="workflow",
        data={"event": "message", "data": {"node_type": "LLM", "text": "x"}},
    )
    a2a = agent_event_to_a2a(ev, "task-1", "conv-1")
    assert isinstance(a2a, TaskArtifactUpdateEvent)
    d = artifact_data(a2a)
    assert d["type"] == "sub_task"  # 上一跳据此识别"已盖章"帧透传
    assert d["sub_task_path"] == ["A", "wf:a"]
    assert d["node_kind"] == "workflow"
    assert d["data"] == {"event": "message", "data": {"node_type": "LLM", "text": "x"}}


# ── 向后兼容：既有事件映射不变（无 sub_task 信封）───────────────────────────


def test_plain_agent_event_unchanged_no_sub_task():
    a2a = agent_event_to_a2a(ThinkChunkEvent(content="hi"), "task-1", "conv-1")
    assert isinstance(a2a, TaskArtifactUpdateEvent)
    d = artifact_data(a2a)
    assert d["type"] == "think_chunk"
    assert d.get("type") != "sub_task"
    # content 同时写入 text part
    texts = [p.text for p in a2a.artifact.parts if p.WhichOneof("content") == "text"]
    assert texts == ["hi"]
