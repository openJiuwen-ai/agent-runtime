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
