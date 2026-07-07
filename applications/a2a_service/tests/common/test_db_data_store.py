# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""DbDataStore / CacheBackedDataStore 持久化功能单元测试。

覆盖场景：
1. CacheBackedDataStore 写入流程（先 DB 后 Redis cache）
2. CacheBackedDataStore 读取流程（cache hit / cache miss -> DB fallback -> 回填）
3. CacheBackedDataStore Redis 故障时 DB 回源
4. KvAdapter 适配层 put/get/delete
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from common.data_store import DataRecord
from common.db_data_store import DbDataStore
from common.cache_backed_data_store import CacheBackedDataStore
from common.kv_adapter import KvAdapter


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

@dataclass
class _MockRow:
    """模拟数据库行记录"""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


class _MockDbHandler:
    """模拟 DB handler"""

    def __init__(self):
        self._store: dict[tuple[str, str], _MockRow] = {}
        self.call_log: list[str] = []

    async def get(self, table_name: str, filters: dict[str, Any]) -> Optional[_MockRow]:
        ns = str(filters.get("state_domain", ""))
        key = str(filters.get("state_key", ""))
        self.call_log.append(f"get:{ns}:{key}")
        return self._store.get((ns, key))

    async def create(self, table_name: str, data: dict[str, Any]) -> Any:
        ns = str(data.get("state_domain", ""))
        key = str(data.get("state_key", ""))
        self.call_log.append(f"create:{ns}:{key}")
        row = _MockRow(data=dict(data))
        self._store[(ns, key)] = row
        return row

    async def update(self, table_name: str, filters: dict[str, Any], data: dict[str, Any]) -> Any:
        ns = str(filters.get("state_domain", ""))
        key = str(filters.get("state_key", ""))
        self.call_log.append(f"update:{ns}:{key}")
        existing = self._store.get((ns, key))
        if existing is None:
            return None
        existing.data.update(data)
        return existing

    async def delete(self, table_name: str, filters: dict[str, Any]) -> bool:
        ns = str(filters.get("state_domain", ""))
        key = str(filters.get("state_key", ""))
        self.call_log.append(f"delete:{ns}:{key}")
        if (ns, key) in self._store:
            del self._store[(ns, key)]
            return True
        return False


class _MockRedisCache:
    """模拟 Redis 缓存"""

    def __init__(self):
        self._store: dict[str, Any] = {}
        self._fail = False

    def set_fail(self, fail: bool = True):
        self._fail = fail

    async def get_json(self, key: str) -> Optional[Any]:
        if self._fail:
            raise ConnectionError("Redis unavailable")
        return self._store.get(key)

    async def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        if self._fail:
            raise ConnectionError("Redis unavailable")
        self._store[key] = value

    async def delete(self, *keys: str) -> None:
        if self._fail:
            raise ConnectionError("Redis unavailable")
        for k in keys:
            self._store.pop(k, None)


# ---------------------------------------------------------------------------
# 1. CacheBackedDataStore 写入流程（先 DB 后 Redis cache）
# ---------------------------------------------------------------------------

class TestCacheBackedDataStoreWrite(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.db_handler = _MockDbHandler()
        self.db_store = DbDataStore(self.db_handler)
        self.cache = _MockRedisCache()
        self.store = CacheBackedDataStore(
            db_store=self.db_store,
            cache_store=self.cache,
            key_prefix="runtime",
        )

    async def test_write_persists_to_db(self):
        """写入后 DB 有数据"""
        await self.store.write("task", "t1", {"task_id": "abc"})
        record = await self.db_store.read("task", "t1")
        self.assertIsNotNone(record)
        self.assertEqual(record.value, {"task_id": "abc"})

    async def test_write_refreshes_cache(self):
        """写入后 cache 有数据"""
        await self.store.write("task", "t1", {"task_id": "abc"})
        cached = await self.cache.get_json("runtime:task:t1")
        self.assertEqual(cached, {"task_id": "abc"})

    async def test_write_db_and_cache_consistent(self):
        """写入的 DB 数据和 cache 数据一致"""
        test_value = {"task_id": "abc", "status": "running", "meta": {"k": "v"}}
        await self.store.write("task", "t1", test_value, ttl_seconds=600)
        db_record = await self.db_store.read("task", "t1")
        cached = await self.cache.get_json("runtime:task:t1")
        self.assertEqual(db_record.value, test_value)
        self.assertEqual(cached, test_value)

    async def test_redis_failure_write_still_persists_db(self):
        """Redis 故障时，写入仍持久化到 DB"""
        self.cache.set_fail(True)
        await self.store.write("task", "t1", {"task_id": "abc"})
        record = await self.db_store.read("task", "t1")
        self.assertEqual(record.value, {"task_id": "abc"})


# ---------------------------------------------------------------------------
# 2. CacheBackedDataStore 读取流程（cache hit / cache miss -> DB fallback -> 回填）
# ---------------------------------------------------------------------------

class TestCacheBackedDataStoreRead(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.db_handler = _MockDbHandler()
        self.db_store = DbDataStore(self.db_handler)
        self.cache = _MockRedisCache()
        self.store = CacheBackedDataStore(
            db_store=self.db_store,
            cache_store=self.cache,
            key_prefix="runtime",
        )

    async def test_read_cache_hit(self):
        """读取时 cache 命中，不查 DB"""
        await self.store.write("task", "t1", {"task_id": "abc"})
        self.db_handler.call_log.clear()

        record = await self.store.read("task", "t1")
        self.assertEqual(record.value, {"task_id": "abc"})
        self.assertNotIn("get:task:t1", self.db_handler.call_log)

    async def test_read_cache_miss_db_hit_backfill(self):
        """读取时 cache miss，回源 DB 并回填 cache"""
        await self.store.write("task", "t1", {"task_id": "abc"})
        await self.cache.delete("runtime:task:t1")
        self.db_handler.call_log.clear()

        record = await self.store.read("task", "t1")
        self.assertEqual(record.value, {"task_id": "abc"})
        self.assertIn("get:task:t1", self.db_handler.call_log)
        # 验证回填
        cached = await self.cache.get_json("runtime:task:t1")
        self.assertEqual(cached, {"task_id": "abc"})

    async def test_read_cache_miss_db_miss(self):
        """读取时 cache miss 且 DB miss，返回 None"""
        self.assertIsNone(await self.store.read("task", "nope"))


# ---------------------------------------------------------------------------
# 3. CacheBackedDataStore Redis 故障时 DB 回源
# ---------------------------------------------------------------------------

class TestCacheBackedDataStoreRedisFailure(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.db_handler = _MockDbHandler()
        self.db_store = DbDataStore(self.db_handler)
        self.cache = _MockRedisCache()
        self.store = CacheBackedDataStore(
            db_store=self.db_store,
            cache_store=self.cache,
            key_prefix="runtime",
        )

    async def test_redis_failure_read_fallback_db(self):
        """Redis 故障时，读取回源 DB"""
        await self.store.write("task", "t1", {"task_id": "abc"})
        self.cache.set_fail(True)
        self.db_handler.call_log.clear()

        record = await self.store.read("task", "t1")
        self.assertEqual(record.value, {"task_id": "abc"})
        self.assertIn("get:task:t1", self.db_handler.call_log)

    async def test_redis_failure_remove_still_deletes_db(self):
        """Redis 故障时删除，DB 仍能删除"""
        await self.store.write("task", "t1", {"task_id": "abc"})
        self.cache.set_fail(True)
        await self.store.remove("task", "t1")
        self.assertIsNone(await self.db_store.read("task", "t1"))

    async def test_redis_crash_recovery(self):
        """Redis 故障后恢复：写入 -> Redis 崩溃 -> 读取(DB回源) -> Redis 恢复 -> 读取(cache命中)"""
        await self.store.write("task", "t1", {"task_id": "abc", "status": "running"})
        self.cache.set_fail(True)
        record = await self.store.read("task", "t1")
        self.assertEqual(record.value, {"task_id": "abc", "status": "running"})
        self.cache.set_fail(False)
        record = await self.store.read("task", "t1")
        self.assertEqual(record.value, {"task_id": "abc", "status": "running"})


# ---------------------------------------------------------------------------
# 4. KvAdapter 适配层 put/get/delete
# ---------------------------------------------------------------------------

class TestKvAdapter(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.db_handler = _MockDbHandler()
        self.db_store = DbDataStore(self.db_handler)
        self.cache = _MockRedisCache()
        self.store = CacheBackedDataStore(
            db_store=self.db_store,
            cache_store=self.cache,
        )
        self.adapter = KvAdapter(self.store, namespace="session_task", default_ttl_seconds=1800)

    async def test_put_and_get(self):
        """put + get 基本流程"""
        await self.adapter.put("conv-123", {"task_id": "t1"})
        self.assertEqual((await self.adapter.get("conv-123")), {"task_id": "t1"})

    async def test_get_nonexistent(self):
        """get 不存在的 key 返回 None"""
        self.assertIsNone(await self.adapter.get("nope"))

    async def test_delete(self):
        """delete 后 get 返回 None"""
        await self.adapter.put("conv-123", {"task_id": "t1"})
        await self.adapter.delete("conv-123")
        self.assertIsNone(await self.adapter.get("conv-123"))

    async def test_namespace_isolation(self):
        """不同 namespace 的数据互不干扰"""
        adapter_a = KvAdapter(self.store, namespace="session_task")
        adapter_b = KvAdapter(self.store, namespace="session_request")
        await adapter_a.put("key1", {"v": "a"})
        await adapter_b.put("key1", {"v": "b"})
        self.assertEqual((await adapter_a.get("key1")), {"v": "a"})
        self.assertEqual((await adapter_b.get("key1")), {"v": "b"})

    async def test_default_ttl(self):
        """使用默认 TTL"""
        await self.adapter.put("conv-123", {"v": 1})
        record = await self.db_store.read("session_task", "conv-123")
        self.assertIsNotNone(record.ttl_seconds)

    async def test_custom_ttl_override(self):
        """自定义 TTL 覆盖默认值"""
        await self.adapter.put("conv-123", {"v": 1}, ttl_seconds=60)
        record = await self.db_store.read("session_task", "conv-123")
        self.assertLessEqual(record.ttl_seconds, 60)


if __name__ == "__main__":
    unittest.main()
