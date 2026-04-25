# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Todolist tool implementations."""

import json
from typing import AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Input, Tool, ToolCard
from openjiuwen.core.session.agent import Session

from .manager import TodoListManager
from .models import TodoItem, TodoList, TodoStatus, ToolOutput
from .tool_card import (
    TODO_CREATE_CARD,
    TODO_MODIFY_CARD,
    TODO_QUERY_CARD,
)


class TodoToolError(Exception):
    """Todolist 工具异常基类"""
    pass


class TodoToolBase(Tool):
    """任务清单工具基类 - 通过Session State接口管理"""

    # Manager 实例（无状态，每次调用方法时传入 session）
    _manager = TodoListManager()

    def __init__(self, card: ToolCard):
        super().__init__(card)


def _parse_tasks_from_string(tasks_str: str) -> list[str]:
    """解析分号分隔的任务字符串

    Args:
        tasks_str: 分号分隔的任务字符串

    Returns:
        任务内容列表
    """
    if not tasks_str:
        return []
    # 按分号分隔，过滤空字符串
    return [t.strip() for t in tasks_str.split(";") if t.strip()]


def _parse_tasks_from_json(json_str: str) -> list[str]:
    """解析 JSON 格式的任务字符串

    Args:
        json_str: JSON 格式的任务字符串

    Returns:
        任务内容列表
    """
    if not json_str:
        return []
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            result = []
            for item in data:
                if isinstance(item, dict):
                    content = item.get("content", "")
                else:
                    content = str(item)
                if content:
                    result.append(content)
            return result
        return []
    except json.JSONDecodeError:
        logger.warning(f"[Todolist] Failed to parse JSON tasks: {json_str}")
        return []


def _format_todolist(todolist: TodoList) -> str:
    """格式化任务清单为可读字符串

    Args:
        todolist: 任务列表

    Returns:
        格式化后的字符串
    """
    if not todolist:
        return "任务清单为空"

    lines = []
    for task in todolist:
        status_icon = {
            TodoStatus.PENDING: "[ ]",
            TodoStatus.IN_PROGRESS: "[>]",
            TodoStatus.COMPLETED: "[√]",
            TodoStatus.CANCELLED: "[-]",
            TodoStatus.FAILED: "[x]"
        }.get(task.status, "[?]")

        line = f"{status_icon} index={task.index}: {task.content}"
        if task.activeForm:
            line += f" ({task.activeForm})"
        if task.status == TodoStatus.COMPLETED and task.result:
            line += f" -> {task.result}"
        lines.append(line)

    return "\n".join(lines)


class TodoCreateTool(TodoToolBase):
    """创建任务工具 - 继承标准Tool基类"""

    def __init__(self):
        super().__init__(TODO_CREATE_CARD)

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """执行任务创建

        Args:
            inputs: 输入参数，支持 tasks(分号分隔) 或 json_tasks(JSON格式)
            **kwargs: 包含 session

        Returns:
            创建结果
        """
        session: Session = kwargs.get("session")
        if not session:
            raise build_error(
                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                reason="TodoCreateTool requires a valid session in kwargs"
            )

        # 解析输入
        if isinstance(inputs, dict):
            tasks_str = inputs.get("tasks", "")
            json_tasks_str = inputs.get("json_tasks", "")
            active_form = inputs.get("activeForm", "")
            task_result = inputs.get("result", "")
        else:
            tasks_str = ""
            json_tasks_str = ""
            active_form = ""
            task_result = ""

        # 解析任务内容
        tasks = []
        if json_tasks_str:
            tasks = _parse_tasks_from_json(json_tasks_str)
        if not tasks and tasks_str:
            tasks = _parse_tasks_from_string(tasks_str)

        if not tasks:
            return ToolOutput(
                success=False,
                data=None,
                error="No tasks provided. Please provide either 'tasks' or 'json_tasks' parameter."
            )

        # 创建任务
        try:
            new_tasks = await self._manager.create_todolist(session, tasks, activate_first=False)

            # 如果传入了 activeForm 或 result，更新新创建的任务
            if active_form or task_result:
                start_idx = new_tasks[0].index if new_tasks else 0
                todolist = await self._manager.load(session)
                for task in todolist:
                    if task.index >= start_idx:  # 新创建的任务
                        if active_form:
                            task.activeForm = active_form
                        if task_result:
                            task.result = task_result
                await self._manager.save(session, todolist)
                # 重新获取更新后的任务
                new_tasks = [t for t in todolist if t.index >= start_idx]

            result_text = f"成功创建 {len(new_tasks)} 个任务:\n"
            for task in new_tasks:
                result_text += f"  - index={task.index}: {task.content}\n"

            return ToolOutput(
                success=True,
                data={
                    "tasks": [task.model_dump() for task in new_tasks],
                    "count": len(new_tasks),
                    "formatted": result_text
                },
                error=None
            )
        except Exception as e:
            logger.error(f"[Todolist] Failed to create tasks: {e}")
            return ToolOutput(
                success=False,
                data=None,
                error=str(e)
            )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[ToolOutput]:
        """流式输出(当前不支持)"""
        raise NotImplementedError("TodoCreateTool does not support streaming")


class TodoQueryTool(TodoToolBase):
    """查询任务工具"""

    def __init__(self):
        super().__init__(TODO_QUERY_CARD)

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """执行任务查询

        Args:
            inputs: 输入参数，支持 status 过滤
            **kwargs: 包含 session

        Returns:
            查询结果
        """
        session: Session = kwargs.get("session")
        if not session:
            raise build_error(
                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                reason="TodoQueryTool requires a valid session in kwargs"
            )

        # 解析输入
        if isinstance(inputs, dict):
            status_str = inputs.get("status", "")
            include_completed = inputs.get("include_completed", True)
        else:
            status_str = ""
            include_completed = True

        try:
            if status_str:
                # 按状态过滤
                try:
                    status = TodoStatus(status_str)
                except ValueError:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error=f"Invalid status: {status_str}. Must be one of: {[s.value for s in TodoStatus]}"
                    )

                todolist = await self._manager.get_tasks_by_status(session, status, include_completed)
            else:
                # 返回全部
                todolist = await self._manager.load(session)

            formatted = _format_todolist(todolist)

            return ToolOutput(
                success=True,
                data={
                    "tasks": [task.model_dump() for task in todolist],
                    "count": len(todolist),
                    "formatted": formatted
                },
                error=None
            )
        except Exception as e:
            logger.error(f"[Todolist] Failed to query tasks: {e}")
            return ToolOutput(
                success=False,
                data=None,
                error=str(e)
            )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[ToolOutput]:
        """流式输出(当前不支持)"""
        raise NotImplementedError("TodoQueryTool does not support streaming")


class TodoModifyTool(TodoToolBase):
    """修改任务工具（含追加/删除）"""

    def __init__(self):
        super().__init__(TODO_MODIFY_CARD)

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """执行任务修改

        Args:
            inputs: 输入参数，包含 action 和其他参数
            **kwargs: 包含 session

        Returns:
            修改结果
        """
        session: Session = kwargs.get("session")
        if not session:
            raise build_error(
                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                reason="TodoModifyTool requires a valid session in kwargs"
            )

        # 解析输入
        if isinstance(inputs, dict):
            action = inputs.get("action", "")
            index = inputs.get("index")
            tasks_str = inputs.get("tasks", "")
            json_tasks_str = inputs.get("json_tasks", "")
            activate_immediately = inputs.get("activate_immediately", False)
            updates = inputs.get("updates", {})
        else:
            action = ""
            index = None
            tasks_str = ""
            json_tasks_str = ""
            activate_immediately = False
            updates = {}

        if not action:
            return ToolOutput(
                success=False,
                data=None,
                error="Action is required. Must be one of: start, complete, fail, cancel, update, delete, append"
            )

        try:
            if action == "append":
                # 追加新任务
                tasks = []
                if json_tasks_str:
                    tasks = _parse_tasks_from_json(json_tasks_str)
                if not tasks and tasks_str:
                    tasks = _parse_tasks_from_string(tasks_str)

                if not tasks:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error="No tasks provided for append action"
                    )

                # 加载现有任务列表，追加新任务
                todolist = await self._manager.load(session)
                start_index = len(todolist) + 1

                new_tasks = []
                for i, content in enumerate(tasks):
                    new_task = TodoItem(
                        index=start_index + i,
                        content=content,
                        status=TodoStatus.PENDING
                    )
                    todolist.append(new_task)
                    new_tasks.append(new_task)

                # 处理 activate_immediately
                if activate_immediately:
                    has_in_progress = any(task.status == TodoStatus.IN_PROGRESS for task in todolist)
                    if not has_in_progress and new_tasks:
                        new_tasks[0].status = TodoStatus.IN_PROGRESS

                await self._manager.save(session, todolist)

                result_text = f"成功追加 {len(new_tasks)} 个任务:\n"
                for task in new_tasks:
                    result_text += f"  - index={task.index}: {task.content}\n"

                return ToolOutput(
                    success=True,
                    data={
                        "tasks": [task.model_dump() for task in new_tasks],
                        "count": len(new_tasks),
                        "formatted": result_text
                    },
                    error=None
                )

            elif action == "delete":
                # 删除任务
                if index is None:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error="Index is required for delete action"
                    )

                success = await self._manager.delete_task(session, index)
                if success:
                    return ToolOutput(
                        success=True,
                        data={"index": index, "action": "deleted"},
                        error=None
                    )
                else:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error=f"Task with index {index} not found"
                    )

            elif action in ("start", "complete", "fail", "cancel", "update"):
                # 需要 index 参数的操作
                if index is None:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error=f"Index is required for {action} action"
                    )

                # 获取任务
                task = await self._manager.get_task_by_index(session, index)
                if not task:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error=f"Task with index {index} not found"
                    )

                if action == "start":
                    # 检查是否可启动（线性顺序约束）
                    if not await self._manager.can_start_task(session, index):
                        # 检查是否已有其他 IN_PROGRESS 任务
                        in_progress = await self._manager.get_in_progress_task(session)
                        if in_progress:
                            return ToolOutput(
                                success=False,
                                data=None,
                                error=f"Cannot start task {index}: task {in_progress.index} is already in progress"
                            )
                        # 检查前序任务
                        return ToolOutput(
                            success=False,
                            data=None,
                            error=f"Cannot start task {index}: previous tasks must be completed first"
                        )

                    # 检查是否已有 IN_PROGRESS 任务（单一 IN_PROGRESS 约束）
                    in_progress = await self._manager.get_in_progress_task(session)
                    if in_progress and in_progress.index != index:
                        return ToolOutput(
                            success=False,
                            data=None,
                            error=f"Cannot start task {index}: task {in_progress.index} is already in progress"
                        )

                    task.status = TodoStatus.IN_PROGRESS

                elif action == "complete":
                    task.status = TodoStatus.COMPLETED
                    # 完成任务时，activeForm 需要置空
                    task.activeForm = ""

                elif action == "fail":
                    task.status = TodoStatus.FAILED

                elif action == "cancel":
                    task.status = TodoStatus.CANCELLED

                # Apply updates for status change actions (start, complete, fail, cancel, update)
                if updates and action in ("start", "complete", "fail", "cancel", "update"):
                    for key, value in updates.items():
                        if key == "status":
                            # status 字段必须是有效的 TodoStatus 类型，不允许通过 updates 覆盖，而是由 action 参数内部管理
                            raise build_error(
                                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                                reason=f"Cannot manually update 'status' field via updates. Status is managed internally by action '{action}'."
                            )
                        if hasattr(task, key):
                            setattr(task, key, value)

                # 加载完整的 todolist，将修改后的 task 更新到列表中，然后保存
                todolist = await self._manager.load(session)
                for t in todolist:
                    if t.index == task.index:
                        t.status = task.status
                        # 复制其他可能修改的字段（如 result）
                        if hasattr(task, 'result'):
                            t.result = task.result
                        if hasattr(task, 'activeForm'):
                            t.activeForm = task.activeForm
                        break
                await self._manager.save(session, todolist)
                return ToolOutput(
                    success=True,
                    data={"task": task.model_dump()},
                    error=None
                )

            else:
                return ToolOutput(
                    success=False,
                    data=None,
                    error=f"Unknown action: {action}"
                )

        except Exception as e:
            logger.error(f"[Todolist] Failed to modify task: {e}")
            return ToolOutput(
                success=False,
                data=None,
                error=str(e)
            )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[ToolOutput]:
        """流式输出(当前不支持)"""
        raise NotImplementedError("TodoModifyTool does not support streaming")


# 工具实例创建函数
def create_todolist_tools() -> list[Tool]:
    """创建所有 todolist 工具实例

    Returns:
        工具实例列表
    """
    return [
        TodoCreateTool(),
        TodoQueryTool(),
        TodoModifyTool(),
    ]


__all__ = [
    "TodoCreateTool",
    "TodoQueryTool",
    "TodoModifyTool",
    "create_todolist_tools",
    "TodoToolError",
]