# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
user_router 序列化链路集成测试（Phase 2）。

验证 _extract_event_meta + _serialize_event 的联动行为，使用真实 A2A protobuf 事件。
"""
from __future__ import annotations

import json

import pytest
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    ROLE_AGENT,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.struct_pb2 import Struct, Value

from orchestrator.user_router import _extract_event_meta, _serialize_event


AGENT_ID = "fcbcd0ce-73b0-4097-a0cb-6286341f88f6"
CONV_ID = "90d40c85-cca4-43fe-8e3f-9ad3717fb1b4"
TASK_ID = "task-123"


# ════════════════════════════════════════════════════════════════════
# 辅助：构造真实 protobuf 事件
# ════════════════════════════════════════════════════════════════════


def _data_part(data: dict) -> Part:
    struct = Struct()
    struct.update(data)
    value = Value()
    value.struct_value.CopyFrom(struct)
    part = Part()
    part.data.CopyFrom(value)
    return part


def _build_agent_artifact_event(event_type: str, content: str, extra: dict | None = None) -> TaskArtifactUpdateEvent:
    """模拟 agent_adapter 生成的 Pattern A 事件。"""
    data = {"type": event_type, "content": content}
    if extra:
        data.update(extra)

    parts: list[Part] = []
    if content:
        parts.append(Part(text=content))
    parts.append(_data_part(data))
    artifact = Artifact(artifact_id="art-1", parts=parts)
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONV_ID,
        artifact=artifact,
        last_chunk=False,
    )


def _build_workflow_artifact_event(event_kind: str, node_data: dict) -> TaskArtifactUpdateEvent:
    """模拟 VersatileAdapter 交付的解包后工作流帧。

    data part 形状：``{"event": "<kind>", "data": <node_data>}``
    —— event_kind 为 Versatile 上游的 ``message`` / ``end``。
    """
    parts = [_data_part({"event": event_kind, "data": node_data})]
    artifact = Artifact(artifact_id="art-wf", parts=parts)
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONV_ID,
        artifact=artifact,
        last_chunk=False,
    )


def _build_completed_status_event(text: str) -> TaskStatusUpdateEvent:
    """模拟 FinalAnswerEnd → TaskStatusUpdateEvent(COMPLETED)。"""
    msg = Message(role=ROLE_AGENT, message_id="m-1", parts=[Part(text=text)])
    return TaskStatusUpdateEvent(
        task_id=TASK_ID,
        context_id=CONV_ID,
        status=TaskStatus(state=TASK_STATE_COMPLETED, message=msg),
    )


def _build_failed_status_event() -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=TASK_ID,
        context_id=CONV_ID,
        status=TaskStatus(state=TASK_STATE_FAILED),
    )


# ════════════════════════════════════════════════════════════════════
# _extract_event_meta — Pattern A 识别
# ════════════════════════════════════════════════════════════════════


def test_extract_agent_event_conversation_start():
    ev = _build_agent_artifact_event("conversation_start", "本轮对话开始")
    meta = _extract_event_meta(ev)
    assert meta == {
        "kind": "agent",
        "type": "conversation_start",
        "content": "本轮对话开始",
        "data": {},  # content 和 type 已被剔出
        "plugin": "",
    }


def test_extract_agent_event_think_chunk_preserves_other_data():
    ev = _build_agent_artifact_event(
        "todolist_item",
        "1.推荐理财产品（待执行）<br/>",
        extra={"id": 1, "title": "推荐理财产品", "status": "pending"},
    )
    meta = _extract_event_meta(ev)
    assert meta["kind"] == "agent"
    assert meta["type"] == "todolist_item"
    assert meta["content"] == "1.推荐理财产品（待执行）<br/>"
    # data 里保留 id/title/status，剔除 type 和 content
    # protobuf Struct 把 int 存成 float（1 → 1.0），这里宽松比对
    assert meta["data"]["title"] == "推荐理财产品"
    assert meta["data"]["status"] == "pending"


# ════════════════════════════════════════════════════════════════════
# _extract_event_meta — workflow 事件识别（按 data part 形状 {event,data}）
# ════════════════════════════════════════════════════════════════════


def test_extract_workflow_message_returns_kind_workflow():
    node_data = {
        "text": '{"SPTRANSRETCODE":"00009"}',
        "index": "0",
        "node_id": "node_xxx",
        "node_type": "QA",
        "node_name": "问答_获取灰度策略",
        "workflow_id": "wf-1",
    }
    ev = _build_workflow_artifact_event("message", node_data)
    meta = _extract_event_meta(ev)
    assert meta["kind"] == "workflow"
    assert meta["type"] == "message"
    assert meta["content"] == ""
    assert meta["data"]["node_type"] == "QA"
    assert meta["data"]["node_name"] == "问答_获取灰度策略"


def test_extract_workflow_end_node_inside_message_is_still_workflow():
    """node_type=End 的节点帧仍是 workflow message（是节点类型而非流结束）。"""
    node_data = {
        "text": "",
        "summary": "",
        "node_id": "node_end",
        "node_type": "End",
        "node_name": "结束",
        "is_finished": True,
        "workflow_id": "wf-1",
    }
    ev = _build_workflow_artifact_event("message", node_data)
    meta = _extract_event_meta(ev)
    assert meta["kind"] == "workflow"
    assert meta["type"] == "message"
    assert meta["data"]["node_type"] == "End"


def test_extract_workflow_end_event_returns_none():
    """上游 event=end 是流结束信号，不作为北向事件发出，由 [DONE] 收尾。"""
    ev = _build_workflow_artifact_event("end", {})
    assert _extract_event_meta(ev) is None


# ════════════════════════════════════════════════════════════════════
# _extract_event_meta — spec 外事件透传（与 AgentEngine 对齐，见 D-1）
# ════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "event_type",
    ["conversation_end", "summary", "thought", "answer", "delegate"],
)
def test_extract_spec_external_events_pass_through(event_type):
    """spec §2.3 未枚举但实现内部会发的事件（conversation_end / summary 等），
    跟 AgentEngine 对齐——照样透出去，不做屏蔽。
    """
    ev = _build_agent_artifact_event(event_type, "internal")
    meta = _extract_event_meta(ev)
    assert meta is not None
    assert meta["kind"] == "agent"
    assert meta["type"] == event_type


def test_serialize_spec_external_event_emits_frame():
    """端到端：spec 外事件仍产出完整 SSE 帧。"""
    ev = _build_agent_artifact_event("summary", "内部流片段")
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed["custom_rsp_data"]["event"] == "summary"
    assert parsed["custom_rsp_data"]["content"] == "内部流片段"


# ════════════════════════════════════════════════════════════════════
# _extract_event_meta — TaskStatusUpdateEvent 处理
# ════════════════════════════════════════════════════════════════════


def test_extract_completed_becomes_final_answer_end():
    ev = _build_completed_status_event("本轮任务全部完成")
    meta = _extract_event_meta(ev)
    assert meta == {
        "kind": "agent",
        "type": "final_answer_end",
        "content": "本轮任务全部完成",
        "data": {},
        "plugin": "",
    }


def test_extract_failed_returns_none():
    """FAILED 状态不向前端推送（当前行为）。"""
    ev = _build_failed_status_event()
    assert _extract_event_meta(ev) is None


# ════════════════════════════════════════════════════════════════════
# _serialize_event — 串联 wrap_*
# ════════════════════════════════════════════════════════════════════


def test_serialize_agent_event_full_wrapping():
    ev = _build_agent_artifact_event("conversation_start", "本轮对话开始")
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed["success"] is True
    assert parsed["agent_id"] == AGENT_ID
    assert parsed["conversation_id"] == CONV_ID
    assert parsed["output"] == ""
    assert parsed["error"] == ""
    assert isinstance(parsed["execution_time"], (int, float))
    assert parsed["custom_rsp_data"]["event"] == "conversation_start"
    assert parsed["custom_rsp_data"]["content"] == "本轮对话开始"


def test_serialize_think_chunk_has_numeric_execution_time():
    """对齐 spec §2.3.3：think_chunk 的 execution_time 是数字。"""
    ev = _build_agent_artifact_event("think_chunk", "我来")
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    parsed = json.loads(payload)
    assert isinstance(parsed["execution_time"], (int, float))
    assert parsed["execution_time"] > 0


def test_serialize_planning_execution_process_has_error_code():
    """决策 2：planning_execution_process 带 error_code: ""。"""
    ev = _build_agent_artifact_event(
        "planning_execution_process",
        "[执行轨迹] 正在执行步骤1: 理财产品推荐",
    )
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    parsed = json.loads(payload)
    assert "error_code" in parsed
    assert parsed["error_code"] == ""


def test_serialize_other_agent_event_has_no_error_code():
    ev = _build_agent_artifact_event("conversation_start", "")
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    parsed = json.loads(payload)
    assert "error_code" not in parsed


def test_serialize_workflow_message_event():
    node_data = {
        "text": '{"SPTRANSRETCODE":"00009","INSTRUCTIONKEY":"GET_GRAY_INFO"}',
        "index": "0",
        "node_id": "node_xxx",
        "node_type": "QA",
        "node_name": "问答_获取灰度策略",
        "workflow_id": "wf-1",
    }
    ev = _build_workflow_artifact_event("message", node_data)
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    parsed = json.loads(payload)
    # workflow 帧不带 output/error/error_code
    assert "output" not in parsed
    assert "error" not in parsed
    assert "error_code" not in parsed
    assert parsed["custom_rsp_data"]["event"] == "message"
    assert parsed["custom_rsp_data"]["data"]["node_type"] == "QA"
    # data.text 里的内嵌 JSON 字符串应该原样保留
    inner = json.loads(parsed["custom_rsp_data"]["data"]["text"])
    assert inner["INSTRUCTIONKEY"] == "GET_GRAY_INFO"


def test_serialize_workflow_end_event_returns_none():
    """上游 end 帧不该产出北向事件，由 [DONE] 收尾。"""
    ev = _build_workflow_artifact_event("end", {})
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    assert payload is None


def test_serialize_tool_end_preserves_plugin_name():
    """tool_end 事件的 plugin 必须保留为工具名（query_balance），不被后续空串覆盖；
    custom_rsp_data 里不能出现重复的 plugin 键。
    """
    ev = _build_agent_artifact_event(
        "tool_end",
        "query_balance 执行完成",
        extra={"plugin": "query_balance", "data": {}},
    )
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    parsed = json.loads(payload)
    inner = parsed["custom_rsp_data"]
    # plugin 只能出现一次，且值是工具名
    assert inner.get("plugin") == "query_balance"
    # custom_rsp_data.data 是业务载荷本身，不是 {plugin, data} 的嵌套
    assert inner["data"] == {}


def test_serialize_completed_status_becomes_final_answer_end_frame():
    ev = _build_completed_status_event("任务完成")
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    parsed = json.loads(payload)
    assert parsed["custom_rsp_data"]["event"] == "final_answer_end"
    assert parsed["custom_rsp_data"]["content"] == "任务完成"


def test_serialize_failed_status_returns_none():
    """FAILED 事件不产出 SSE 帧。"""
    ev = _build_failed_status_event()
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    assert payload is None


def test_serialize_monotonic_elapsed():
    """连续两次序列化，execution_time 单调递增。"""
    import time

    ev = _build_agent_artifact_event("todolist_start", "规划任务清单")
    t0 = time.monotonic()
    p1 = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=t0,
    )
    time.sleep(0.01)
    p2 = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=t0,
    )
    e1 = json.loads(p1)["execution_time"]
    e2 = json.loads(p2)["execution_time"]
    assert e2 > e1


# ════════════════════════════════════════════════════════════════════
# 抓包对齐（端到端）
# ════════════════════════════════════════════════════════════════════


def test_end_to_end_capture_conversation_start():
    """从 protobuf event 一路序列化到 SSE JSON 字段，对齐抓包。"""
    ev = _build_agent_artifact_event("conversation_start", "本轮对话开始")
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    parsed = json.loads(payload)
    # 检查抓包里明确有的字段
    assert parsed["agent_id"] == AGENT_ID
    assert parsed["conversation_id"] == CONV_ID
    assert parsed["custom_rsp_data"]["event"] == "conversation_start"
    assert parsed["custom_rsp_data"]["content"] == "本轮对话开始"
    assert parsed["custom_rsp_data"]["latency"] == ""
    assert parsed["custom_rsp_data"]["plugin"] == ""
