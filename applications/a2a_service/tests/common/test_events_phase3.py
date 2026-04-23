"""
Phase 3 事件补齐单测（common/events.py）。

验证：
  - PlanningExecutionProcessEvent 已注册到 AgentEvent / EVENT_TYPE_MAP
  - ToolStatusEvent / TodoStartEvent / TodoStatusEvent 已定义且可构造
"""
from __future__ import annotations

import pytest

from common.events import (
    AgentEvent,
    EVENT_TYPE_MAP,
    PlanningExecutionProcessEvent,
    ToolStartEvent,
    ToolStatusEvent,
    TodoStartEvent,
    TodoStatusEvent,
)


def test_planning_execution_process_event_construction():
    ev = PlanningExecutionProcessEvent(
        content="[执行轨迹] 正在执行步骤1: 理财产品推荐 (tool=product_recommend_skill)",
    )
    assert ev.type == "planning_execution_process"
    assert "[执行轨迹]" in ev.content


def test_planning_execution_process_registered_in_event_type_map():
    assert "planning_execution_process" in EVENT_TYPE_MAP
    assert EVENT_TYPE_MAP["planning_execution_process"] is PlanningExecutionProcessEvent


def test_todo_start_event_construction():
    ev = TodoStartEvent(id=1, title="挑选理财产品", content="挑选理财产品")
    assert ev.type == "todo_start"
    assert ev.id == 1
    assert ev.title == "挑选理财产品"


def test_todo_status_event_construction():
    ev = TodoStatusEvent(id=1, status="in_progress", content="正在挑选理财产品")
    assert ev.type == "todo_status"
    assert ev.status == "in_progress"


def test_tool_status_event_construction():
    ev = ToolStatusEvent(
        plugin="product_recommend_skill",
        content="正在调用工作流获取理财产品列表",
    )
    assert ev.type == "tool_status"
    assert ev.plugin == "product_recommend_skill"


def test_tool_start_and_status_are_different_types():
    start = ToolStartEvent(content="开始调用", plugin="x")
    status = ToolStatusEvent(content="正在调用", plugin="x")
    assert start.type == "tool_start"
    assert status.type == "tool_status"
