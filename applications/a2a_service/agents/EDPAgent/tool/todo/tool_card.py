# coding:utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Todolist tool card definitions with complete descriptions."""

from openjiuwen.core.foundation.tool import ToolCard

# ==============================================================================
# TODO-CREATE 描述
# ==============================================================================
TODO_CREATE_DESCRIPTION = """
创建当前会话的待办事项列表，用于跟踪进度、组织复杂任务，帮助用户了解整体执行情况。

## 何时使用

主动在以下场景调用：
- 任务需要 3 个或更多步骤
- 用户提供多个待完成事项（编号列表、逗号或分号分隔）
- 用户明确要求使用待办清单
- 任务具有规划性质（多步骤实现、功能开发等）

识别到规划需求后，立即调用本工具。

## 何时不使用

- 单个简单任务
- 纯信息查询或对话
- 可在 3 步以内完成的琐碎任务

## 使用方式

用分号(;)分隔多个任务：
    {
        "tasks": "设计用户界面;实现接口集成;添加单元测试"
    }

或使用 JSON 格式：
    {
        "json_tasks": [{"content": "设计用户界面"}, {"content": "实现接口集成"}]
    }

## 规则

- 第一个任务自动设为 in_progress，其余为 pending
- 同一时间只能有一个 in_progress 任务
- 任务描述必须具体、可执行、清晰明确
- 调用本工具会覆盖当前会话的任务列表；若需追加任务，请使用 todo_modify
"""

TODO_CREATE_PARAMS = {
    "tasks": "任务列表（仅支持分号分隔）。示例：'创建登录表单;实现表单验证;添加错误处理'",
    "json_tasks": "JSON格式任务列表，格式为[{\"content\": \"任务描述\"}, ...]",
}

# ==============================================================================
# TODO-QUERY 描述
# ==============================================================================
TODO_QUERY_DESCRIPTION = """
检索并显示当前会话的所有待办事项。

## 何时使用 todo_query

使用 todo_query 的场景：
- 每次调用 todo_modify 修改任务后，必须调用此工具向用户展示当前任务进度
- 需要查看当前任务全貌和各任务索引，再决定如何更新
- 不确定当前有哪些任务处于 in_progress 或 pending

## 返回格式

返回当前所有任务列表，包含每个任务的：
- index: 任务索引（从1开始）
- content: 任务描述
- status: 任务状态（pending/in_progress/completed/cancelled/failed）
"""

TODO_QUERY_PARAMS = {
    "status": "可选，支持按状态过滤任务，可选值：pending, in_progress, completed, cancelled, failed",
    "include_completed": "是否包含已完成的任务，默认为 true",
}

# ==============================================================================
# TODO-MODIFY 描述
# ==============================================================================
TODO_MODIFY_DESCRIPTION = """
修改当前会话的待办事项的状态、内容和执行结果等。支持批量操作，尽量将多个变更合并为一次调用。

## 核心用途

更新（update）、删除（delete）、取消（cancel）、追加（append）、启动（start）、完成（complete）、失败（fail）

使用 todo_modify 的场景（不需要先调用 todo_query）：
- 已知任务索引，直接更新状态、内容或追加新任务
- 任务刚完成，立即标记为 completed

## 重要说明

- 若需重新规划整个任务列表，请调用 todo_create
- 支持批量操作，尽量将多个变更合并为一次调用，避免连续多次调用

## action 支持的操作类型

### start - 启动任务
将指定任务设置为 in_progress 状态（仅当 index=1 或前序任务全部 COMPLETED 时才能成功）：
    {
        "action": "start",
        "index": 2
    }
建议配合 updates 参数设置 activeForm，描述当前正在做什么：
    {
        "action": "start",
        "index": 1,
        "updates": {"activeForm": "正在收集旅游景点信息..."}
    }

### complete - 完成任务
将指定任务标记为 completed，并自动激活下一个 pending 任务：
    {
        "action": "complete",
        "index": 1
    }
建议配合 updates 参数设置 result，保存任务执行结果：
    {
        "action": "complete",
        "index": 1,
        "updates": {"result": "已收集到10个热门景点信息：平遥古城、五台山、云冈石窟..."}
    }

### fail - 标记失败
将指定任务标记为 failed：
    {
        "action": "fail",
        "index": 1
    }

### cancel - 取消任务
将指定任务标记为 cancelled（任务将被忽略，不再执行）：
    {
        "action": "cancel",
        "index": 2
    }

### update - 更新任务
修改现有任务的描述：
    {
        "action": "update",
        "index": 1,
        "tasks": "新的任务描述"
    }

部分字段更新：
    {
        "action": "update",
        "index": 1,
        "updates": {"content": "更新后的描述"}
    }

### delete - 删除任务
从列表中永久删除指定任务：
    {
        "action": "delete",
        "index": 2
    }

### append - 追加任务
在列表末尾追加新任务（可单条或批量）：
    {
        "action": "append",
        "tasks": "新任务1；新任务2"
    }

或使用 JSON 格式：
    {
        "action": "append",
        "json_tasks": [{"content": "新任务1"}, {"content": "新任务2"}],
        "activate_immediately": true  # 是否立即激活第一个新任务
    }

## 核心规则

- 线性队列：任务按加入顺序形成队列，通过 index 确定位置
- 执行顺序：index=1 -> index=2 -> ...
- 仅当 index=1 或前序任务全部 COMPLETED 时才能 start
- 同一时间只能有一个 in_progress 任务
"""

TODO_MODIFY_PARAMS = {
    "action": "操作类型：start(启动)、complete(完成)、fail(失败)、cancel(取消)、update(更新)、delete(删除)、append(追加)",
    "index": "任务索引（从1开始），除 append 外其他操作都需要",
    "tasks": "任务内容(分号分隔)，用于 append 或 update 操作",
    "json_tasks": "JSON格式任务列表，用于 append 操作，格式为[{\"content\": \"任务描述\"}, ...]",
    "activate_immediately": "是否立即激活第一个新任务（仅 append 时有效），默认为 false",
    "updates": "要更新的字段，格式：{\"field\": \"value\"}，仅 update 操作有效",
}


def _build_create_params() -> dict:
    p = TODO_CREATE_PARAMS
    return {
        "type": "object",
        "properties": {
            "tasks": {"type": "string", "description": p["tasks"]},
            "json_tasks": {"type": "string", "description": p["json_tasks"]},
        },
        "required": [],
    }


def _build_query_params() -> dict:
    p = TODO_QUERY_PARAMS
    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "cancelled", "failed"],
                "description": p["status"],
            },
            "include_completed": {
                "type": "boolean",
                "default": True,
                "description": p["include_completed"],
            },
        },
        "required": [],
    }


def _build_modify_params() -> dict:
    p = TODO_MODIFY_PARAMS
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "complete", "fail", "cancel", "update", "delete", "append"],
                "description": p["action"],
            },
            "index": {
                "type": "integer",
                "description": p["index"],
            },
            "tasks": {
                "type": "string",
                "description": p["tasks"],
            },
            "json_tasks": {
                "type": "string",
                "description": p["json_tasks"],
            },
            "activate_immediately": {
                "type": "boolean",
                "default": False,
                "description": p["activate_immediately"],
            },
            "updates": {
                "type": "object",
                "description": p["updates"],
            },
        },
        "required": ["action"],
    }


# ==============================================================================
# ToolCard 定义
# ==============================================================================
TODO_CREATE_CARD = ToolCard(
    id="todolist_create",
    name="todolist_create",
    description=TODO_CREATE_DESCRIPTION.strip(),
    input_params=_build_create_params(),
)

TODO_QUERY_CARD = ToolCard(
    id="todolist_query",
    name="todolist_query",
    description=TODO_QUERY_DESCRIPTION.strip(),
    input_params=_build_query_params(),
)

TODO_MODIFY_CARD = ToolCard(
    id="todolist_modify",
    name="todolist_modify",
    description=TODO_MODIFY_DESCRIPTION.strip(),
    input_params=_build_modify_params(),
)

# 所有 ToolCard 列表
TODOLIST_CARDS = [
    TODO_CREATE_CARD,
    TODO_QUERY_CARD,
    TODO_MODIFY_CARD,
]


__all__ = [
    "TODO_CREATE_CARD",
    "TODO_QUERY_CARD",
    "TODO_MODIFY_CARD",
    "TODOLIST_CARDS",
]