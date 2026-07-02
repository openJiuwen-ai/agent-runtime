# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Redis-backed A2A TaskStore.

Task protobuf 序列化为 base64 二进制存入 Redis，key 格式：a2a:task:{task_id}。
TTL 复用 redis_session_ttl 配置（秒），默认 1800 s。
"""
from __future__ import annotations

import base64
from typing import Optional

from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task
from loguru import logger

from common.redis_client import RedisClient

_KEY_PREFIX = "a2a:task:"


class RedisTaskStore(TaskStore):
    def __init__(self, redis: RedisClient, ttl: int = 1800) -> None:
        self._redis = redis
        self._ttl = ttl

    async def save(self, task: Task, context: ServerCallContext) -> None:
        key = _KEY_PREFIX + task.id
        data = base64.b64encode(task.SerializeToString()).decode("ascii")
        await self._redis.set(key, data, ex=self._ttl)
        logger.debug(f"[TaskStore] save task={task.id} state={task.status.state}")

    async def get(self, task_id: str, context: ServerCallContext) -> Optional[Task]:
        if not task_id:
            return None
        raw = await self._redis.get(_KEY_PREFIX + task_id)
        if raw is None:
            return None
        task = Task()
        task.ParseFromString(base64.b64decode(raw))
        return task

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._redis.delete(_KEY_PREFIX + task_id)
        logger.debug(f"[TaskStore] delete task={task_id}")

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        return ListTasksResponse()


class ReadOnlyTaskStore(TaskStore):
    """只读 TaskStore 包装器：读/删/列表委托给底层 store，save 为空操作。

    用于 SDK DefaultRequestHandler，避免 SDK 的 TaskManager 对每个流式事件
    都全量序列化 Task 写入 Redis（数千次 save 雪崩）。
    Task 状态持久化由应用层 TaskStateManager 统一管理：
    - create_task / update_task_status → save 到 Redis
    - finalize_completed → save COMPLETED 到 Redis
    SDK 仅在内存中维护 _current_task 处理事件流，artifacts 通过 SSE 实时透传上游，
    无需持久化。
    """

    def __init__(self, inner: TaskStore) -> None:
        self._inner = inner

    async def save(self, task: Task, context: ServerCallContext) -> None:
        # 空操作：SDK 的事件级 save 不写 Redis，由应用层 TaskStateManager 管理状态持久化
        pass

    async def get(self, task_id: str, context: ServerCallContext) -> Optional[Task]:
        return await self._inner.get(task_id, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._inner.delete(task_id, context)

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        return await self._inner.list(params, context)
