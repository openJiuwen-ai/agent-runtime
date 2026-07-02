# coding: utf-8
from __future__ import annotations

import json

import pytest
from a2a.types.a2a_pb2 import TASK_STATE_COMPLETED, Task, TaskStatus

from common.redis_client import RedisClient
from common.redis_task_store import RedisTaskStore


class _AsyncRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.closed = False
        self.eval_result = 1
        self.set_result = True
        self.calls: list[tuple] = []

    async def ping(self):
        self.calls.append(("ping",))
        return True

    async def aclose(self):
        self.closed = True

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, ex=None, nx=False):
        self.calls.append(("set", key, value, ex, nx))
        if nx and key in self.values:
            return None
        self.values[key] = value
        return self.set_result

    async def delete(self, *keys: str):
        self.calls.append(("delete", keys))
        for key in keys:
            self.values.pop(key, None)

    async def eval(self, *args):
        self.calls.append(("eval", args))
        return self.eval_result


@pytest.mark.asyncio
async def test_redis_client_methods_without_real_redis(monkeypatch):
    fake = _AsyncRedis()
    monkeypatch.setattr("common.redis_client.Redis.from_url", lambda *_args, **_kwargs: fake)

    client = RedisClient()
    with pytest.raises(RuntimeError):
        _ = client.client

    await client.connect("redis://:secret@[::1]:6379/0")
    assert client.client is fake
    await client.set("plain", "value", ex=3)
    assert await client.get("plain") == "value"
    assert await client.set_if_not_exists("nx", "1", ex=1) is True
    assert await client.set_nx("nx-no-ttl", "1") is True
    assert await client.set_nx("nx-ttl", "1", ex=2) is True
    await client.delete("plain")
    assert await client.get("plain") is None

    await client.set_json("json", {"a": "b"})
    assert await client.get_json("json") == {"a": "b"}
    fake.values["bad-json"] = "{"
    assert await client.get_json("bad-json") is None

    assert await client.acquire_lock("lock", "owner", 0) is True
    fake.eval_result = 0
    assert await client.release_lock("lock", "owner") is False
    fake.eval_result = 1
    assert await client.release_lock("lock", "owner") is True

    await client.disconnect()
    assert fake.closed is True


@pytest.mark.asyncio
async def test_redis_task_store_round_trip_and_empty_paths():
    redis = _AsyncRedis()
    store = RedisTaskStore(redis, ttl=9)

    task = Task(id="task-1", context_id="conv-1", status=TaskStatus(state=TASK_STATE_COMPLETED))
    await store.save(task, context=None)

    loaded = await store.get("task-1", context=None)
    assert loaded is not None
    assert loaded.id == "task-1"
    assert loaded.context_id == "conv-1"
    assert loaded.status.state == TASK_STATE_COMPLETED
    assert await store.get("", context=None) is None
    assert await store.get("missing", context=None) is None

    await store.delete("task-1", context=None)
    assert await store.get("task-1", context=None) is None
    listed = await store.list(params=None, context=None)
    assert listed is not None
