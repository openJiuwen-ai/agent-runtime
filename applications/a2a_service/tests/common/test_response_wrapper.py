# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
response_wrapper 单测。

对齐 docs/feat-north-api-sse.md §4.4.3 和 docs/north-api-response-format.md §3。
每个测试名都对应抓包中观察到的一个具体行为。
"""
from __future__ import annotations

import json

import pytest

from common.response_wrapper import (
    wrap_agent_event,
    wrap_error,
    wrap_workflow_event,
)


AGENT_ID = "fcbcd0ce-73b0-4097-a0cb-6286341f88f6"
CONV_ID = "90d40c85-cca4-43fe-8e3f-9ad3717fb1b4"


# ════════════════════════════════════════════════════════════════════
# Pattern A：基础字段
# ════════════════════════════════════════════════════════════════════


def test_agent_event_has_all_outer_fields():
    """外层 7 个字段齐全。

    success / agent_id / conversation_id / output / error / execution_time / custom_rsp_data。
    """
    wrapped = wrap_agent_event(
        event_type="conversation_start",
        content="本轮对话开始",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.007411,
    )
    for key in [
        "success",
        "agent_id",
        "conversation_id",
        "output",
        "error",
        "execution_time",
        "custom_rsp_data",
    ]:
        assert key in wrapped, f"missing outer field: {key}"


def test_agent_event_has_all_inner_fields():
    """内层 custom_rsp_data 6 字段齐全。

    data / event / content / createdTime / latency / plugin。
    """
    wrapped = wrap_agent_event(
        event_type="think_start",
        content="准备进行步骤规划",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.00933,
    )
    inner = wrapped["custom_rsp_data"]
    for key in ["data", "event", "content", "createdTime", "latency", "plugin"]:
        assert key in inner, f"missing inner field: {key}"


def test_agent_event_echoes_ids():
    wrapped = wrap_agent_event(
        event_type="conversation_start",
        content="",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.1,
    )
    assert wrapped["agent_id"] == AGENT_ID
    assert wrapped["conversation_id"] == CONV_ID


def test_agent_event_success_is_true():
    wrapped = wrap_agent_event(
        event_type="conversation_start",
        content="",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.1,
    )
    assert wrapped["success"] is True


def test_agent_event_output_error_are_empty_string():
    wrapped = wrap_agent_event(
        event_type="conversation_start",
        content="",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.1,
    )
    assert wrapped["output"] == ""
    assert wrapped["error"] == ""


def test_agent_event_latency_plugin_are_empty_string():
    """抓包中 latency/plugin 永远是 ""，不做推断。"""
    wrapped = wrap_agent_event(
        event_type="interrupt_start",
        content="请确认",
        data={"interrupt_id": "xxx"},
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=1.5,
    )
    assert wrapped["custom_rsp_data"]["latency"] == ""
    assert wrapped["custom_rsp_data"]["plugin"] == ""


def test_agent_event_data_defaults_to_empty_dict():
    """data=None 时 custom_rsp_data.data 应为 {}。"""
    wrapped = wrap_agent_event(
        event_type="conversation_start",
        content="",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.0,
    )
    assert wrapped["custom_rsp_data"]["data"] == {}


def test_agent_event_data_preserved_when_given():
    wrapped = wrap_agent_event(
        event_type="todolist_item",
        content="1.推荐理财产品（待执行）<br/>",
        data={"id": 1, "title": "推荐理财产品", "status": "pending"},
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=7.27,
    )
    assert wrapped["custom_rsp_data"]["data"] == {
        "id": 1,
        "title": "推荐理财产品",
        "status": "pending",
    }


def test_agent_event_created_time_uses_current_ms_by_default():
    wrapped = wrap_agent_event(
        event_type="conversation_start",
        content="",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.0,
    )
    # epoch ms：2020 年 ≈ 1577e9，2050 年 ≈ 2524e9
    ts = wrapped["custom_rsp_data"]["createdTime"]
    assert isinstance(ts, int)
    assert 1_577_000_000_000 < ts < 2_600_000_000_000


def test_agent_event_created_time_can_be_overridden():
    wrapped = wrap_agent_event(
        event_type="conversation_start",
        content="",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.0,
        created_time_ms=1776678838708,
    )
    assert wrapped["custom_rsp_data"]["createdTime"] == 1776678838708


# ════════════════════════════════════════════════════════════════════
# agent event：所有事件 execution_time 均为数字（对齐北向 spec §2.3.3）
# ════════════════════════════════════════════════════════════════════


def test_think_chunk_execution_time_is_number():
    """对齐 spec：think_chunk 的 execution_time 也是数字，不是空串。"""
    wrapped = wrap_agent_event(
        event_type="think_chunk",
        content="我来",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=1.234,
    )
    assert wrapped["execution_time"] == 1.234


def test_non_think_chunk_execution_time_is_number():
    for event_type in [
        "conversation_start",
        "think_start",
        "think_end",
        "todolist_item",
        "todo_start",
        "tool_start",
        "interrupt_start",
        "final_answer_start",
        "summary",
        "final_answer_chunk",
        "final_answer_end",
        "conversation_end",
    ]:
        wrapped = wrap_agent_event(
            event_type=event_type,
            content="",
            data=None,
            agent_id=AGENT_ID,
            conversation_id=CONV_ID,
            elapsed=1.234,
        )
        assert wrapped["execution_time"] == 1.234, (
            f"{event_type} execution_time 应是数字"
        )


# ════════════════════════════════════════════════════════════════════
# Pattern A：error_code 仅 planning_execution_process 带上（决策 2）
# ════════════════════════════════════════════════════════════════════


def test_planning_execution_process_has_error_code():
    wrapped = wrap_agent_event(
        event_type="planning_execution_process",
        content="[执行轨迹] 正在执行步骤1: 理财产品推荐 (tool=product_recommend_skill)",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=7.28,
    )
    assert "error_code" in wrapped
    assert wrapped["error_code"] == ""


def test_other_events_do_not_have_error_code():
    for event_type in [
        "conversation_start",
        "think_chunk",
        "todolist_item",
        "tool_start",
        "interrupt_start",
        "summary",
        "final_answer_chunk",
        "product_select_progress",  # 业务自定义 type 也不带
    ]:
        wrapped = wrap_agent_event(
            event_type=event_type,
            content="",
            data=None,
            agent_id=AGENT_ID,
            conversation_id=CONV_ID,
            elapsed=0.5,
        )
        assert "error_code" not in wrapped, (
            f"{event_type} 不该有 error_code"
        )


# ════════════════════════════════════════════════════════════════════
# Pattern A：event_type 不受白名单限制（决策 4：通用桥梁）
# ════════════════════════════════════════════════════════════════════


def test_wrapper_accepts_arbitrary_event_type():
    """业务 skill 自定义 type（如 product_select_progress）必须能过。"""
    wrapped = wrap_agent_event(
        event_type="product_select_progress",
        content="正在处理您的选购信息…",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=33.3,
    )
    assert wrapped["custom_rsp_data"]["event"] == "product_select_progress"
    # 自定义 type 默认不进 error_code 白名单
    assert "error_code" not in wrapped


def test_wrapper_accepts_brand_new_event_type():
    """将来新增未知 event type 也应直接通过。"""
    wrapped = wrap_agent_event(
        event_type="brand_new_type_2099",
        content="future event",
        data={"foo": "bar"},
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.0,
    )
    assert wrapped["custom_rsp_data"]["event"] == "brand_new_type_2099"


# ════════════════════════════════════════════════════════════════════
# Pattern B：Workflow 事件包装
# ════════════════════════════════════════════════════════════════════


def test_workflow_event_custom_rsp_data_is_node_data_directly():
    """对齐 AgentEngine default_transform_response：custom_rsp_data 直接是上游节点 dict。

    AgentEngine/src/core/enterprise_dispatch_respmod.py:99-104：
        custom_rsp_data = json.loads(response_text)  # 整段上游 chunk
        result["custom_rsp_data"] = custom_rsp_data
    上游 chunk 是扁平 dict（含 node_type/node_name/text 等），不再包 {event, data}。
    """
    node_data = {
        "text": '{"SPTRANSRETCODE":"00009"}',
        "index": "0",
        "node_id": "node_xxx",
        "node_type": "QA",
        "node_name": "问答_获取灰度策略",
        "workflow_id": "b2c3d4e5",
    }
    wrapped = wrap_workflow_event(
        data=node_data,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=7.537,
    )
    assert wrapped["success"] is True
    assert wrapped["agent_id"] == AGENT_ID
    assert wrapped["conversation_id"] == CONV_ID
    assert wrapped["execution_time"] == 7.537
    # custom_rsp_data 直接是节点数据
    assert wrapped["custom_rsp_data"] == node_data
    # 不再有 {event, data} 两级包装
    assert "event" not in wrapped["custom_rsp_data"]
    assert "data" not in wrapped["custom_rsp_data"]


def test_workflow_event_handles_start_node():
    """mock_workflow_server_v5.py:295 的 Start 节点形态。"""
    node = {"node_type": "Start", "node_name": "StartNode", "conversation_id": "c1"}
    wrapped = wrap_workflow_event(
        data=node,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.05,
    )
    assert wrapped["custom_rsp_data"] == node


def test_workflow_event_handles_llm_node():
    """mock_workflow_server_v5.py:306 的 LLM 节点形态（带 text）。"""
    node = {"text": "正在分析您的资金情况...", "node_type": "LLM", "node_name": "LLM_NODE"}
    wrapped = wrap_workflow_event(
        data=node,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.5,
    )
    assert wrapped["custom_rsp_data"] == node
    assert wrapped["custom_rsp_data"]["text"] == "正在分析您的资金情况..."


def test_workflow_event_outer_has_no_output_error_error_code():
    """对齐 AgentEngine：外层无 output / error / error_code。"""
    wrapped = wrap_workflow_event(
        data={"node_type": "QA"},
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.1,
    )
    assert "output" not in wrapped
    assert "error" not in wrapped
    assert "error_code" not in wrapped


def test_workflow_event_outer_has_only_5_keys():
    """对齐 AgentEngine：外层固定 5 键。"""
    wrapped = wrap_workflow_event(
        data={"node_type": "QA"},
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.1,
    )
    assert set(wrapped.keys()) == {
        "success", "agent_id", "conversation_id",
        "execution_time", "custom_rsp_data",
    }


def test_workflow_event_non_dict_data_falls_back_to_empty_dict():
    """防御：data 不是 dict 时（不会发生但有备无患），custom_rsp_data 兜底为 {}。"""
    wrapped = wrap_workflow_event(
        data=None,  # type: ignore[arg-type]
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.1,
    )
    assert wrapped["custom_rsp_data"] == {}


# ════════════════════════════════════════════════════════════════════
# 错误 / 限流响应
# ════════════════════════════════════════════════════════════════════


def test_error_response_shape():
    wrapped = wrap_error(
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.02,
        error_code="100001",
        error_msg="系统超负载，请稍后重试",
    )
    assert wrapped["success"] is False
    assert wrapped["agent_id"] == AGENT_ID
    assert wrapped["conversation_id"] == CONV_ID
    assert wrapped["execution_time"] == 0.02
    assert wrapped["error_code"] == "100001"
    assert wrapped["error_msg"] == "系统超负载，请稍后重试"
    # 错误响应不含 custom_rsp_data
    assert "custom_rsp_data" not in wrapped


# ════════════════════════════════════════════════════════════════════
# 序列化一致性（前端拿到的 JSON 能按预期解析）
# ════════════════════════════════════════════════════════════════════


def test_wrapped_agent_event_json_round_trip():
    wrapped = wrap_agent_event(
        event_type="todolist_item",
        content="1.推荐理财产品（待执行）<br/>",
        data={"id": 1, "title": "推荐理财产品"},
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=7.28,
    )
    s = json.dumps(wrapped, ensure_ascii=False)
    assert "<br/>" in s, "HTML 片段不应被转义"
    parsed = json.loads(s)
    assert parsed == wrapped


def test_wrapped_workflow_event_handles_nested_json_string_in_text():
    """workflow 节点的 text 是转义过的 JSON 字符串，应保持原样。"""
    inner_json = '{"SPTRANSRETCODE":"00009","INSTRUCTIONKEY":"GET_GRAY_INFO"}'
    wrapped = wrap_workflow_event(
        data={
            "text": inner_json,
            "node_type": "QA",
            "node_id": "node_123",
            "node_name": "灰度策略",
            "workflow_id": "wf-1",
        },
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.1,
    )
    s = json.dumps(wrapped, ensure_ascii=False)
    reparsed = json.loads(s)
    # text 直接挂在 custom_rsp_data 下，不再嵌套 data 一级
    assert reparsed["custom_rsp_data"]["text"] == inner_json
    assert (
        json.loads(reparsed["custom_rsp_data"]["text"])["INSTRUCTIONKEY"]
        == "GET_GRAY_INFO"
    )


# ════════════════════════════════════════════════════════════════════
# 抓包样本对齐（挑 3 个代表帧做 field-by-field 对比）
# ════════════════════════════════════════════════════════════════════


def test_matches_capture_conversation_start():
    """抓包首帧 conversation_start 的字段逐一对齐。"""
    wrapped = wrap_agent_event(
        event_type="conversation_start",
        content="本轮对话开始",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=0.007411,
        created_time_ms=1776678838708,
    )
    expected = {
        "success": True,
        "agent_id": AGENT_ID,
        "conversation_id": CONV_ID,
        "output": "",
        "error": "",
        "execution_time": 0.007411,
        "custom_rsp_data": {
            "data": {},
            "event": "conversation_start",
            "content": "本轮对话开始",
            "createdTime": 1776678838708,
            "latency": "",
            "plugin": "",
        },
    }
    assert wrapped == expected


def test_matches_capture_think_chunk():
    """think_chunk 帧按 spec §2.3.3 execution_time 为数字；无 error_code。"""
    wrapped = wrap_agent_event(
        event_type="think_chunk",
        content="我来",
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=4.5,
        created_time_ms=1776678843148,
    )
    assert wrapped["execution_time"] == 4.5
    assert "error_code" not in wrapped


def test_matches_capture_planning_execution_process():
    """抓包 planning_execution_process 帧：带 error_code 空串。"""
    wrapped = wrap_agent_event(
        event_type="planning_execution_process",
        content=(
            "[执行轨迹] 正在执行步骤1: 理财产品推荐 "
            "(tool=product_recommend_skill)"
        ),
        data=None,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=7.277592,
        created_time_ms=1776678845978,
    )
    assert wrapped["error_code"] == ""
    assert wrapped["execution_time"] == 7.277592


def test_matches_capture_workflow_message():
    """对齐 AgentEngine default_transform_response 的输出帧形态。

    custom_rsp_data 直接是上游节点 dict，无 {event, data} 二级。
    """
    inner_text = (
        '{"SPTRANSRETCODE":"00009","INSTRUCTIONKEY":"GET_GRAY_INFO",'
        '"CURRENTNODE":"理财-课题版灰度策略查询"}'
    )
    node_data = {
        "text": inner_text,
        "index": "0",
        "node_id": "node_1231231231231",
        "node_type": "QA",
        "node_name": "问答_获取灰度策略",
        "workflow_id": "b2c3d4e5-f6a7-8901-bcde-f23456789012",
    }
    wrapped = wrap_workflow_event(
        data=node_data,
        agent_id=AGENT_ID,
        conversation_id=CONV_ID,
        elapsed=7.537642,
    )
    assert wrapped == {
        "success": True,
        "agent_id": AGENT_ID,
        "conversation_id": CONV_ID,
        "execution_time": 7.537642,
        "custom_rsp_data": node_data,
    }
