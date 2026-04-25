# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Todolist manager for Session State operations."""

from typing import List, Optional

from openjiuwen.core.session.agent import Session
from .models import TodoItem, TodoList, TodoStatus


class TodoListManager:
    """任务清单管理器 - 无状态，每次方法调用需传入 session

    设计说明：
    - 不持有 session 引用，避免跨 session 状态混乱
    - 每次方法调用时传入 session，确保操作在正确的 session 上下文中执行
    - 由 Redis Checkpointer 统一持久化，TTL 在 checkpointer 层配置

    用法示例：
        manager = TodoListManager()
        todolist = await manager.load(session)
        await manager.save(session, todolist)
    """

    STATE_KEY = "todolist"

    def __init__(self):
        """初始化 TodoListManager（无状态）"""
        pass

    async def load(self, session: Session) -> TodoList:
        """加载任务清单（从Session State）

        Args:
            session: Session 实例

        Returns:
            任务列表
        """
        data = session.get_state(self.STATE_KEY)
        if not data:
            return []
        # 从字典恢复为 TodoItem 对象列表
        return [TodoItem(**item) if isinstance(item, dict) else item for item in data]

    async def save(self, session: Session, todolist: TodoList) -> None:
        """保存任务清单（通过Session State接口）

        Args:
            session: Session 实例
            todolist: 任务列表
        """
        # 显式转换为字典列表，确保与 Redis 存储兼容
        # 使用 mode='json' 强制将枚举转换为字符串值
        session.update_state({
            self.STATE_KEY: [item.model_dump(mode='json') if hasattr(item, 'model_dump') else item for item in todolist]
        })

    async def create_todolist(
        self,
        session: Session,
        contents: List[str],
        activate_first: bool = False
    ) -> List[TodoItem]:
        """批量创建任务

        Args:
            session: Session 实例
            contents: 任务描述列表
            activate_first: 是否立即激活第一个新任务

        Returns:
            创建的任务列表
        """
        todolist = []
        # index 从 1 开始
        start_index = len(todolist) + 1
        new_tasks = []

        for i, content in enumerate(contents):
            new_task = TodoItem(
                index=start_index + i,
                content=content,
                status=TodoStatus.PENDING
            )
            todolist.append(new_task)
            new_tasks.append(new_task)

        # 如果需要立即激活第一个新任务
        if activate_first and new_tasks:
            # 检查是否已有 IN_PROGRESS 任务
            has_in_progress = any(task.status == TodoStatus.IN_PROGRESS for task in todolist)
            if not has_in_progress:
                new_tasks[0].status = TodoStatus.IN_PROGRESS

        await self.save(session, todolist)
        return new_tasks

    async def get_task_by_index(self, session: Session, index: int) -> Optional[TodoItem]:
        """根据索引获取任务

        Args:
            session: Session 实例
            index: 任务索引

        Returns:
            TodoItem 或 None
        """
        todolist = await self.load(session)
        for task in todolist:
            if task.index == index:
                return task
        return None

    async def get_next_pending_task(self, session: Session) -> Optional[TodoItem]:
        """获取下一个待执行的任务（线性队列顺序）

        Args:
            session: Session 实例

        Returns:
            下一个待执行的任务或 None
        """
        todolist = await self.load(session)
        for task in todolist:
            if task.status == TodoStatus.PENDING:
                return task
        return None

    async def get_in_progress_task(self, session: Session) -> Optional[TodoItem]:
        """获取当前执行中的任务

        注意：同一时间只能有一个任务处于 IN_PROGRESS 状态

        Args:
            session: Session 实例

        Returns:
            执行中的任务或 None
        """
        todolist = await self.load(session)
        for task in todolist:
            if task.status == TodoStatus.IN_PROGRESS:
                return task
        return None

    async def get_tasks_by_status(
        self,
        session: Session,
        status: TodoStatus,
        include_completed: bool = True
    ) -> TodoList:
        """根据状态获取任务列表

        Args:
            session: Session 实例
            status: 任务状态
            include_completed: 是否包含已完成的任务

        Returns:
            符合条件的任务列表
        """
        todolist = await self.load(session)
        result = []
        for task in todolist:
            if task.status == status:
                result.append(task)
            elif status != TodoStatus.COMPLETED and task.status == TodoStatus.COMPLETED and include_completed:
                # include_completed 为 True 时允许查询 completed 状态
                if status == TodoStatus.COMPLETED:
                    result.append(task)
        return result

    async def update_task(
        self,
        session: Session,
        index: int,
        updates: dict
    ) -> Optional[TodoItem]:
        """更新任务内容

        Args:
            session: Session 实例
            index: 任务索引
            updates: 要更新的字段字典

        Returns:
            更新后的任务或 None
        """
        todolist = await self.load(session)
        for task in todolist:
            if task.index == index:
                for key, value in updates.items():
                    if hasattr(task, key):
                        setattr(task, key, value)
                await self.save(session, todolist)
                return task
        return None

    async def delete_task(self, session: Session, index: int) -> bool:
        """删除任务

        Args:
            session: Session 实例
            index: 任务索引

        Returns:
            是否成功删除
        """
        todolist = await self.load(session)
        for i, task in enumerate(todolist):
            if task.index == index:
                todolist.pop(i)
                # 重新索引，从 1 开始
                for j, t in enumerate(todolist):
                    t.index = j + 1
                await self.save(session, todolist)
                return True
        return False

    async def can_start_task(self, session: Session, index: int) -> bool:
        """检查任务是否可以启动（线性顺序约束）

        约束规则：
        - index=1 的任务可以直接启动
        - 其他任务必须等到前序任务全部 COMPLETED 才能启动

        Args:
            session: Session 实例
            index: 任务索引（从 1 开始）

        Returns:
            是否可以启动
        """
        if index == 1:
            return True
        todolist = await self.load(session)
        # 检查前序任务是否全部完成
        for task in todolist:
            if task.index < index and task.status != TodoStatus.COMPLETED:
                return False
        return True

    async def delete(self, session: Session) -> None:
        """删除任务清单

        Args:
            session: Session 实例
        """
        session.update_state({self.STATE_KEY: None})


__all__ = [
    "TodoListManager",
]