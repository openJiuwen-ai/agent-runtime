# coding: utf-8
from __future__ import annotations

import json

import pytest
from a2a.types.a2a_pb2 import TASK_STATE_COMPLETED, Task, TaskStatus
from redis.exceptions import ResponseError, WatchError

from common.redis_client import RedisClient
from common.redis_task_store import RedisTaskStore


class _AsyncRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.closed = False
        self.eval_result = 1
        self.set_result = True
        self.eval_error: Exception | None = None
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
        if self.eval_error is not None:
            raise self.eval_error
        return self.eval_result

    def pipeline(self, transaction: bool = True) -> "_WatchPipeline":
        return _WatchPipeline(self)


class _WatchPipeline:
    """单键 WATCH/MULTI/EXEC 语义的最小桩（release_lock 降级路径用）。"""

    def __init__(self, redis: "_AsyncRedis") -> None:
        self._redis = redis
        self._watched: dict[str, str | None] = {}
        self._queued: list[tuple] = []
        self._in_multi = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def watch(self, *keys: str):
        for key in keys:
            self._watched[key] = self._redis.values.get(key)

    async def unwatch(self):
        self._watched.clear()

    def multi(self):
        self._in_multi = True

    async def get(self, key: str):
        return self._redis.values.get(key)

    def delete(self, *keys: str):
        self._queued.append(("delete", keys))

    async def execute(self):
        for key, snap in self._watched.items():
            if self._redis.values.get(key) != snap:
                raise WatchError(f"watched key changed: {key}")
        resp = []
        for op, keys in self._queued:
            if op == "delete":
                n = 0
                for key in keys:
                    if key in self._redis.values:
                        self._redis.values.pop(key, None)
                        n += 1
                resp.append(n)
        self._queued.clear()
        return resp


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
async def test_release_lock_falls_back_to_watch_when_eval_disabled():
    """EVAL 被禁（rename-command / ACL）→ WATCH-CAS 降级 + memo + owner 校验。"""
    fake = _AsyncRedis()
    fake.eval_error = ResponseError(
        "unknown command 'EVAL', with args beginning with: ...")
    client = RedisClient()
    client._client = fake

    await fake.set("lk", "owner-1")
    # 首次撞 eval → 降级 WATCH 成功释放 + memo
    assert await client.release_lock("lk", "owner-1") is True
    assert client._eval_disabled is True
    assert fake.values.get("lk") is None
    # memo 生效：不再撞 eval（后续直接 WATCH 路径）
    await fake.set("lk", "owner-2")
    assert await client.release_lock("lk", "owner-1") is False  # 非 owner 不删
    assert fake.values.get("lk") == "owner-2"
    assert await client.release_lock("lk", "owner-2") is True
    assert fake.values.get("lk") is None
    # 锁已不在 → False
    assert await client.release_lock("lk", "owner-2") is False


@pytest.mark.asyncio
async def test_release_lock_watch_error_returns_false():
    """WATCH 竞争改写 → 事务未提交 → False（与 Lua 失锁路径一致）。"""
    fake = _AsyncRedis()
    fake.eval_error = ResponseError("NOPERM this user has no permissions to run the 'eval' command")
    client = RedisClient()
    client._client = fake
    await fake.set("lk", "owner-1")

    orig_pipeline = fake.pipeline

    class _Rigged(_WatchPipeline):
        async def get(self, key: str):
            val = await super().get(key)
            # 复读后、EXEC 前插入竞争写者
            self._redis.values["lk"] = "clobbered"
            return val

    fake.pipeline = lambda transaction=True: _Rigged(fake)  # type: ignore[assignment]
    try:
        assert await client.release_lock("lk", "owner-1") is False
        assert fake.values.get("lk") == "clobbered"  # 写集未落
    finally:
        fake.pipeline = orig_pipeline  # type: ignore[assignment]


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
