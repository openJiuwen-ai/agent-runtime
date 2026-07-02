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

from api.dispatch import _extract_answer_from_events, _extract_event_meta, _serialize_event


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


def _build_failed_status_event(text: str = "") -> TaskStatusUpdateEvent:
    """模拟 VA 上游报错 / 内部异常 → TaskStatusUpdateEvent(FAILED)。

    错误描述放在 status.message.parts[0].text，由 _extract_event_meta 转成
    interrupt_start 帧的 content/error 字段。
    """
    status = TaskStatus(state=TASK_STATE_FAILED)
    if text:
        msg = Message(role=ROLE_AGENT, message_id="m-failed", parts=[Part(text=text)])
        status = TaskStatus(state=TASK_STATE_FAILED, message=msg)
    return TaskStatusUpdateEvent(
        task_id=TASK_ID,
        context_id=CONV_ID,
        status=status,
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


def test_extract_workflow_end_event_passes_through():
    """上游 event=end 帧目前由 Runtime 透传给前端（已知 TODO：未来下沉到 VA 侧过滤）。

    历史背景：旧实现在 user_router 硬编码 ``if event_kind == "end": return None``，
    违反"业务语义不进 Runtime"原则。已在 PR-320 review v3 #7 中删除该硬编码，
    后续 PR 会把过滤下沉到 versatile_adapter 的 _process_chunk。
    """
    ev = _build_workflow_artifact_event("end", {})
    meta = _extract_event_meta(ev)
    assert meta is not None
    assert meta["kind"] == "workflow"
    assert meta["type"] == "end"


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


def test_extract_completed_is_internal_control_status_not_display_frame():
    ev = _build_completed_status_event("本轮任务全部完成")
    meta = _extract_event_meta(ev)
    assert meta is None


def test_extract_failed_with_message_becomes_interrupt_start():
    """FAILED 状态映射为 interrupt_start agent 事件，对齐 AgentEngine。

    AgentEngine 用 ``custom_rsp_data.event=interrupt_start`` 配合顶层
    ``success: false`` + ``error: <msg>`` 把上游报错或内部异常告诉前端，本框架对齐。
    """
    ev = _build_failed_status_event("执行报错，错误码：103104")
    meta = _extract_event_meta(ev)
    assert meta == {
        "kind": "agent",
        "type": "interrupt_start",
        "content": "执行报错，错误码：103104",
        "data": {},
        "plugin": "",
        "success": False,
        "error": "执行报错，错误码：103104",
    }


def test_extract_failed_without_message_still_becomes_interrupt_start():
    """FAILED 没带 message 时也要产出 interrupt_start，content/error 留空字符串。"""
    ev = _build_failed_status_event()
    meta = _extract_event_meta(ev)
    assert meta is not None
    assert meta["kind"] == "agent"
    assert meta["type"] == "interrupt_start"
    assert meta["success"] is False
    assert meta["content"] == ""
    assert meta["error"] == ""


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


def test_serialize_workflow_end_event_passes_through():
    """上游 end 帧目前由 Runtime 透传：序列化产出 custom_rsp_data 载荷（已知 TODO，应下沉到 VA）。

    历史背景：旧实现在 user_router 硬编码过滤该帧；现已删除（PR-320 review v3 #7），
    所以 _serialize_event 会照常产出 custom_rsp_data.event=="end" 的载荷。
    """
    ev = _build_workflow_artifact_event("end", {})
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed["success"] is True
    assert parsed["agent_id"] == AGENT_ID
    assert parsed["conversation_id"] == CONV_ID
    # custom_rsp_data 只含 event 与 data（参见 wrap_workflow_event）
    assert parsed["custom_rsp_data"]["event"] == "end"
    assert parsed["custom_rsp_data"]["data"] == {}


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


def test_serialize_completed_status_is_suppressed():
    ev = _build_completed_status_event("任务完成")
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    assert payload is None


def test_serialize_final_answer_chunk_then_completed_does_not_duplicate_final_answer():
    events = [
        _build_agent_artifact_event("final_answer_chunk", "报告"),
        _build_agent_artifact_event("final_answer_end", ""),
        _build_completed_status_event("报告"),
    ]

    payloads = [
        _serialize_event(
            event, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
        )
        for event in events
    ]
    parsed = [json.loads(payload) for payload in payloads if payload is not None]

    assert [frame["custom_rsp_data"]["event"] for frame in parsed] == [
        "final_answer_chunk",
        "final_answer_end",
    ]
    assert [frame["custom_rsp_data"]["content"] for frame in parsed] == ["报告", ""]


def test_extract_answer_from_events_prefers_display_chunk_over_completed_status():
    events = [
        _build_agent_artifact_event("final_answer_chunk", "报告"),
        _build_agent_artifact_event("final_answer_end", ""),
        _build_completed_status_event("报告"),
    ]

    assert _extract_answer_from_events(events) == "报告"


def test_extract_answer_from_events_falls_back_to_completed_status():
    events = [_build_completed_status_event("兜底答案")]

    assert _extract_answer_from_events(events) == "兜底答案"


def test_serialize_failed_status_becomes_interrupt_start_frame():
    """FAILED 事件产出 success:false + custom_rsp_data.event=interrupt_start，
    与 AgentEngine planning_agent.build_planning_stream_payload 的 interrupt_start 形态对齐。
    """
    ev = _build_failed_status_event("执行报错，错误码：103104，错误信息：xxx")
    payload = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=0.0,
    )
    assert payload is not None
    parsed = json.loads(payload)
    # 顶层带 success:false + error
    assert parsed["success"] is False
    assert parsed["agent_id"] == AGENT_ID
    assert parsed["conversation_id"] == CONV_ID
    assert parsed["output"] == ""
    assert parsed["error"] == "执行报错，错误码：103104，错误信息：xxx"
    # custom_rsp_data 形态对齐 agent event
    assert parsed["custom_rsp_data"]["event"] == "interrupt_start"
    assert parsed["custom_rsp_data"]["content"] == "执行报错，错误码：103104，错误信息：xxx"
    assert parsed["custom_rsp_data"]["data"] == {}
    assert parsed["custom_rsp_data"]["plugin"] == ""


def test_serialize_monotonic_elapsed():
    """连续两次序列化，execution_time 单调递增。"""
    import time

    ev = _build_agent_artifact_event("todolist_start", "规划任务清单")
    now = time.monotonic()
    p1 = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=now - 0.1,
    )
    # Windows ``time.monotonic`` 分辨率约 15.6 ms，sleep 必须充分跨过一个 tick，
    # 否则两次 elapsed 都可能落在同一 tick 边界上（均为 0.0），导致单调断言假阴性。
    time.sleep(0.05)
    p2 = _serialize_event(
        ev, agent_id=AGENT_ID, conversation_id=CONV_ID, start_time=now - 0.2,
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
