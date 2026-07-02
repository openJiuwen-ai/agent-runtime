# coding: utf-8
"""RedisTaskStore 单元测试。

验证 save/get/delete/list 的 protobuf 序列化 + Redis 交互。
通过 mock RedisClient 实现零依赖测试。
"""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import ListTasksRequest, Task, TaskState
from persistence.redis_task_store import RedisTaskStore, _KEY_PREFIX


def _make_task(task_id: str = "t-1", state=TaskState.TASK_STATE_WORKING) -> Task:
    return Task(id=task_id, context_id="ctx-1", status={"state": state})


@pytest.fixture
def mock_redis():
    """模拟 RedisClient，内部用 dict 存储。"""
    store: dict[str, str] = {}

    class _MockRedis:
        async def get(self, key: str):
            return store.get(key)

        async def set(self, key: str, value: str, ex: int | None = None):
            store[key] = value

        async def delete(self, *keys: str):
            for k in keys:
                store.pop(k, None)

    return _MockRedis(), store


@pytest.fixture
def task_store(mock_redis):
    redis_client, _ = mock_redis
    # 包装为 RedisClient 接口
    rc = MagicMock()
    rc.get = redis_client.get
    rc.set = redis_client.set
    rc.delete = redis_client.delete
    return RedisTaskStore(rc, ttl=300), mock_redis[1]


@pytest.fixture
def ctx():
    return ServerCallContext()


class TestRedisTaskStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self, task_store, ctx):
        ts, store = task_store
        task = _make_task("t-save-get")
        await ts.save(task, ctx)
        # 验证 Redis 中 key 正确
        key = _KEY_PREFIX + "t-save-get"
        assert key in store
        # 反序列化验证
        result = await ts.get("t-save-get", ctx)
        assert result is not None
        assert result.id == "t-save-get"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, task_store, ctx):
        ts, _ = task_store
        result = await ts.get("no-such-task", ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_empty_task_id(self, task_store, ctx):
        ts, _ = task_store
        result = await ts.get("", ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, task_store, ctx):
        ts, store = task_store
        task = _make_task("t-del")
        await ts.save(task, ctx)
        assert _KEY_PREFIX + "t-del" in store
        await ts.delete("t-del", ctx)
        assert _KEY_PREFIX + "t-del" not in store

    @pytest.mark.asyncio
    async def test_list_returns_empty(self, task_store, ctx):
        ts, _ = task_store
        result = await ts.list(ListTasksRequest(), ctx)
        # 当前实现返回空 ListTasksResponse
        assert result is not None

    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self, task_store, ctx):
        ts, _ = task_store
        task1 = _make_task("t-over", state=TaskState.TASK_STATE_WORKING)
        await ts.save(task1, ctx)
        task2 = _make_task("t-over", state=TaskState.TASK_STATE_COMPLETED)
        await ts.save(task2, ctx)
        result = await ts.get("t-over", ctx)
        assert result.status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.asyncio
    async def test_key_prefix_unified(self, task_store, ctx):
        """与 a2a_service 统一使用 a2a:task: 前缀，便于多级任务路由。"""
        ts, store = task_store
        task = _make_task("t-prefix")
        await ts.save(task, ctx)
        keys = [k for k in store.keys() if "t-prefix" in k]
        assert len(keys) == 1
        assert keys[0].startswith("a2a:task:")

    @pytest.mark.asyncio
    async def test_save_writes_source_agent(self, task_store, ctx):
        """save 时在 task.metadata 中写入 source_agent=versatile_adapter。"""
        ts, _ = task_store
        task = _make_task("t-source")
        await ts.save(task, ctx)
        restored = await ts.get("t-source", ctx)
        assert restored is not None
        assert restored.metadata.fields["source_agent"].string_value == "versatile_adapter"

    @pytest.mark.asyncio
    async def test_serialization_round_trip(self, task_store, ctx):
        """save → get 反序列化后与原始 Task 字段一致。"""
        ts, _ = task_store
        original = _make_task("t-rt", state=TaskState.TASK_STATE_FAILED)
        await ts.save(original, ctx)
        restored = await ts.get("t-rt", ctx)
        assert restored.id == original.id
        assert restored.context_id == original.context_id
        assert restored.status.state == original.status.state
