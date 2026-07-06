# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""A2A TaskStore 协议适配层，将通用 DataStore 适配为 A2A 框架的 TaskStore 接口。"""
from __future__ import annotations

import base64
import json
from typing import Optional

from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct
from loguru import logger

from .kv_adapter import KvAdapter


class TaskStoreAdapter(TaskStore):
    """将通用 DataStore 适配为 A2A TaskStore 接口。

    内部通过 KvAdapter(namespace="task") 操作 DataStore，
    Task protobuf 序列化为 base64 后存入 payload.task_blob。

    Args:
        kv_adapter: 底层 KV 读写适配器。
        source_agent: 若非 None，save 时会向 task.metadata 注入 source_agent 字段，
                      用于路由策略识别任务来源（如 "versatile_adapter"）。
    """

    def __init__(self, kv_adapter: KvAdapter, *, source_agent: str | None = None) -> None:
        self._kv = kv_adapter
        self._source_agent = source_agent

    async def save(self, task: Task, context: ServerCallContext) -> None:
        if self._source_agent is not None:
            if not task.HasField("metadata"):
                task.metadata.CopyFrom(Struct())
            meta_dict = {}
            try:
                from google.protobuf.json_format import MessageToDict
                meta_dict = MessageToDict(task.metadata)
            except Exception as e:
                logger.warning(f"[TaskStoreAdapter] MessageToDict failed: {e}")
            meta_dict["source_agent"] = self._source_agent
            new_meta = Struct()
            new_meta.update(meta_dict)
            task.metadata.CopyFrom(new_meta)

        payload = {
            "task_blob": base64.b64encode(task.SerializeToString()).decode("ascii"),
            "status_state": str(task.status.state),
            "context_id": str(task.context_id or ""),
        }
        await self._kv.put(
            task.id,
            payload,
            metadata={"codec": "a2a-protobuf-base64", "schema_version": "1"},
        )

    async def get(self, task_id: str, context: ServerCallContext) -> Optional[Task]:
        if not task_id:
            return None
        payload = await self._kv.get(task_id)
        if payload is None:
            return None

        if isinstance(payload, dict) and isinstance(payload.get("value"), dict):
            payload = payload["value"]
        elif isinstance(payload, str):
            try:
                parsed = json.loads(payload)
                payload = parsed if isinstance(parsed, dict) else {"value": parsed}
            except Exception:
                payload = {"value": payload}

        blob = str(payload.get("task_blob") or "")
        if not blob:
            legacy_dict = payload.get("task") if isinstance(payload, dict) else None
            if legacy_dict is None and isinstance(payload, dict) and isinstance(payload.get("value"), dict):
                legacy_dict = payload.get("value")
            if isinstance(legacy_dict, dict):
                task = Task()
                ParseDict(legacy_dict, task)
                return task
            return None
        task = Task()
        task.ParseFromString(base64.b64decode(blob))
        return task

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._kv.delete(task_id)

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        return ListTasksResponse()
