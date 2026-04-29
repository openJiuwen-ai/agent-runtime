# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
北向响应包装器。

把内部 A2A / EDPAgent 事件包装成前端可见的 JSON 格式，对齐
docs/feat-north-api-sse.md §4.4.3 与 docs/north-api-response-format.md §3.

两种包装：
  - agent event：EDPAgent 内部事件（think/todolist/tool/interrupt/summary 等）
  - workflow event：Versatile 工作流节点转发事件（event=message/end）

还包含错误/限流场景的简化响应（wrap_error）。

字段语义请直接对齐规范文档：

  1. `execution_time` 对所有 agent event 均为数字（秒），对齐 spec §2.3.3；
     workflow event 同样为数字。
  2. `error_code` 只在 event_type == "planning_execution_process" 时带上空串；
     其他 agent event 不带此字段；workflow event 也不带。
  3. `output` / `error` 仅 agent event 有，固定空串；workflow event 不带。
  4. `latency` 字段保留为空串，为历史预留字段；
     `plugin` 字段在 tool_* agent event 上承载工具名，其他事件为空串。

本模块不依赖 a2a SDK，只用标准库。
"""
from __future__ import annotations

import time
from typing import Any

# ════════════════════════════════════════════════════════════════════
# 事件类型白名单
# ════════════════════════════════════════════════════════════════════

# 这类事件的包装器会额外带一个 `error_code: ""` 字段。
# 对齐抓包观察：其他事件帧均不带 error_code；限流/错误走 wrap_error。
_EVENTS_WITH_ERROR_CODE: frozenset[str] = frozenset({
    "planning_execution_process",
})

# （历史上 think_chunk 曾被设为 ""，与抓包对齐；现统一以 spec §2.3.3 为准，数字）
_EVENTS_WITH_EMPTY_EXECUTION_TIME: frozenset[str] = frozenset()


# ════════════════════════════════════════════════════════════════════
# agent event 包装
# ════════════════════════════════════════════════════════════════════


def wrap_agent_event(
    event_type: str,
    content: str,
    data: dict[str, Any] | None,
    *,
    agent_id: str,
    conversation_id: str,
    elapsed: float,
    created_time_ms: int | None = None,
    plugin: str = "",
    success: bool = True,
    error: str = "",
) -> dict[str, Any]:
    """包装 EDPAgent 内部事件（think / todolist / tool / interrupt / summary 等）。

    event_type 可以是任意字符串，本函数不做白名单过滤：
    业务 skill 发的自定义 type（如 `product_select_progress`）也会被正确包装。

    Args:
        event_type: 事件类型，对应 custom_rsp_data.event。
        content: 事件人类可读文本（可含 HTML 片段，如 todolist_item 的 ``<br/>``）。
        data: 结构化补充，对应 custom_rsp_data.data。None / {} 都可以。
        agent_id: 当前请求的 agent_id，回显给客户端。
        conversation_id: 当前请求的 conversation_id。
        elapsed: 本次 turn 累计耗时秒数（通常 `time.monotonic() - start`）。
        created_time_ms: 事件产生的 epoch 毫秒；默认取当前时间。
        plugin: 工具名（tool_* 事件专用），填入 custom_rsp_data.plugin；默认空串。
        success: 顶层 ``success`` 字段。VA 上游报错或内部异常映射为 interrupt_start
            帧时设为 False，对齐 AgentEngine planning_agent 的失败帧形态。
        error: 顶层 ``error`` 字段。仅 success=False 时承载错误描述。

    Returns:
        可直接 json.dumps 的 dict，格式对齐规范文档 §4.4.3（agent event）。
    """
    exec_time: float | str = (
        "" if event_type in _EVENTS_WITH_EMPTY_EXECUTION_TIME else elapsed
    )
    wrapped: dict[str, Any] = {
        "success": success,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "output": "",
        "error": error,
        "execution_time": exec_time,
        "custom_rsp_data": {
            "data": data or {},
            "event": event_type,
            "content": content,
            "createdTime": (
                created_time_ms
                if created_time_ms is not None
                else int(time.time() * 1000)
            ),
            "latency": "",
            "plugin": plugin,
        },
    }
    if event_type in _EVENTS_WITH_ERROR_CODE:
        wrapped["error_code"] = ""
    return wrapped


# ════════════════════════════════════════════════════════════════════
# workflow event 包装
# ════════════════════════════════════════════════════════════════════


def wrap_workflow_event(
    event_kind: str,
    data: dict[str, Any],
    *,
    agent_id: str,
    conversation_id: str,
    elapsed: float,
) -> dict[str, Any]:
    """包装从 Versatile 转发的工作流节点事件。

    workflow event 比 agent event 字段更少：**无 output / error / error_code 字段**，
    custom_rsp_data 只含 event 和 data 两个键。

    Args:
        event_kind: "message" 或 "end"。
        data: Versatile 节点数据（含 text / node_id / node_type / node_name /
            workflow_id / summary? / is_finished? 等字段）。event_kind=="end"
            时通常为空 dict。
        agent_id / conversation_id: 同 agent event。
        elapsed: 累计秒数。

    Returns:
        可 json.dumps 的 dict，格式对齐规范文档 §3.3（workflow event）。
    """
    if event_kind not in ("message", "end"):
        # 防御：只接受观察到的两个值；其他值也允许透传，避免阻塞将来协议扩展
        pass
    return {
        "success": True,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "execution_time": elapsed,
        "custom_rsp_data": {
            "event": event_kind,
            "data": data,
        },
    }


# ════════════════════════════════════════════════════════════════════
# 错误 / 限流响应包装
# ════════════════════════════════════════════════════════════════════


def wrap_error(
    *,
    agent_id: str,
    conversation_id: str,
    elapsed: float,
    error_code: str,
    error_msg: str,
) -> dict[str, Any]:
    """限流、内部错误等场景的简化响应。

    与成功帧相比：``success`` 为 False，带 ``error_code`` 和 ``error_msg``，
    没有 ``custom_rsp_data``。当前限流分支（user_router.py:434-440）已经是
    这种结构，保持兼容。

    Args:
        error_code: 业务错误码（如限流的 "100001"）。
        error_msg: 错误中文描述。
    """
    return {
        "success": False,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "execution_time": elapsed,
        "error_code": error_code,
        "error_msg": error_msg,
    }
