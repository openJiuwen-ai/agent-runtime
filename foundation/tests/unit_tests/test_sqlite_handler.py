# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SQLite Handler 单元测试"""

import os
import tempfile
import unittest

from sqlalchemy import inspect

from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)


class TestSQLiteHandler(unittest.IsolatedAsyncioTestCase):
    """SQLite Handler 测试类"""

    async def asyncSetUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), "test_sqlite.db")
        self.handler = SQLiteHandler(self.db_path)
        await self.handler.connect()

        self.test_table_def = TableDefinition(
            table_name="test_table",
            columns=[
                ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
                ColumnDefinition("name", "string", length=100, nullable=False),
                ColumnDefinition("value", "string", length=255, nullable=True),
            ],
            indexes=[
                IndexDefinition(["name"], unique=False),
            ],
        )

    async def asyncTearDown(self):
        await self.handler.disconnect()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_connect_disconnect(self):
        """测试连接和断开连接"""
        handler = SQLiteHandler(":memory:")
        await handler.connect()
        self.assertIsNotNone(handler.engine)
        self.assertIsNotNone(handler.session_factory)
        await handler.disconnect()

    async def test_get_table_returns_registered_sqlalchemy_table(self):
        await self.handler.init_table(self.test_table_def)

        table = self.handler.get_table(self.test_table_def.table_name)

        self.assertEqual(table.name, self.test_table_def.table_name)

    def test_get_table_rejects_unregistered_table(self):
        with self.assertRaisesRegex(ValueError, "not initialized"):
            self.handler.get_table("missing_table")

    async def test_init_table(self):
        """测试初始化表"""
        await self.handler.init_table(self.test_table_def)
        self.assertTrue(self.handler.is_table_registered("test_table"))

    async def test_init_table_adds_missing_index_to_existing_table(self):
        """已存在的表缺少声明索引时，仅补建该索引。"""
        table_without_index = TableDefinition(
            table_name="existing_table",
            columns=[
                ColumnDefinition("id", "integer", primary_key=True),
                ColumnDefinition("name", "string", length=100, nullable=False),
            ],
        )
        await self.handler.init_table(table_without_index)

        table_with_index = TableDefinition(
            table_name="existing_table",
            columns=table_without_index.columns,
            indexes=[
                IndexDefinition(
                    ["name"], unique=True, name="uq_existing_table_name"
                )
            ],
        )
        await self.handler.init_table(table_with_index)

        async with self.handler.engine.connect() as conn:
            indexes = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_indexes("existing_table")
            )
        self.assertEqual(
            {(index["name"], bool(index["unique"])) for index in indexes},
            {("uq_existing_table_name", True)},
        )

    async def test_init_table_reuses_equivalent_index_with_legacy_name(self):
        """等价索引即使名称不同，也不应重复创建。"""
        legacy_definition = TableDefinition(
            table_name="legacy_index_table",
            columns=[
                ColumnDefinition("id", "integer", primary_key=True),
                ColumnDefinition("name", "string", length=100, nullable=False),
            ],
            indexes=[
                IndexDefinition(["name"], unique=True, name="uq_legacy_name")
            ],
        )
        await self.handler.init_table(legacy_definition)

        current_definition = TableDefinition(
            table_name="legacy_index_table",
            columns=legacy_definition.columns,
            indexes=[
                IndexDefinition(["name"], unique=True, name="uq_current_name")
            ],
        )
        await self.handler.init_table(current_definition)

        async with self.handler.engine.connect() as conn:
            indexes = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_indexes("legacy_index_table")
            )
        self.assertEqual(
            {index["name"] for index in indexes},
            {"uq_legacy_name"},
        )

    async def test_init_table_does_not_treat_non_unique_index_as_unique(self):
        """相同列的普通索引不能替代声明的唯一索引。"""
        non_unique_definition = TableDefinition(
            table_name="index_constraint_table",
            columns=[
                ColumnDefinition("id", "integer", primary_key=True),
                ColumnDefinition("name", "string", length=100, nullable=False),
            ],
            indexes=[IndexDefinition(["name"], name="ix_non_unique_name")],
        )
        await self.handler.init_table(non_unique_definition)

        unique_definition = TableDefinition(
            table_name="index_constraint_table",
            columns=non_unique_definition.columns,
            indexes=[
                IndexDefinition(["name"], unique=True, name="uq_unique_name")
            ],
        )
        await self.handler.init_table(unique_definition)

        async with self.handler.engine.connect() as conn:
            indexes = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_indexes(
                    "index_constraint_table"
                )
            )
        self.assertEqual(
            {(index["name"], bool(index["unique"])) for index in indexes},
            {("ix_non_unique_name", False), ("uq_unique_name", True)},
        )

    async def test_create(self):
        """测试创建记录"""
        await self.handler.init_table(self.test_table_def)

        record = await self.handler.create("test_table", {"name": "test_name", "value": "test_value"})

        self.assertIsNotNone(record)
        self.assertEqual(record.name, "test_name")
        self.assertEqual(record.value, "test_value")
        self.assertIsNotNone(record.id)

    async def test_get(self):
        """测试获取记录"""
        await self.handler.init_table(self.test_table_def)

        created = await self.handler.create("test_table", {"name": "get_test", "value": "get_value"})

        record = await self.handler.get("test_table", {"id": created.id})

        self.assertIsNotNone(record)
        self.assertEqual(record.name, "get_test")
        self.assertEqual(record.value, "get_value")

    async def test_update(self):
        """测试更新记录"""
        await self.handler.init_table(self.test_table_def)

        created = await self.handler.create("test_table", {"name": "update_test", "value": "old_value"})

        updated = await self.handler.update(
            "test_table",
            {"id": created.id},
            {"value": "new_value"}
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.value, "new_value")

    async def test_delete(self):
        """测试删除记录"""
        await self.handler.init_table(self.test_table_def)

        created = await self.handler.create("test_table", {"name": "delete_test", "value": "delete_value"})

        deleted = await self.handler.delete("test_table", {"id": created.id})
        self.assertTrue(deleted)

        record = await self.handler.get("test_table", {"id": created.id})
        self.assertIsNone(record)

    async def test_list_records(self):
        """测试列表查询"""
        await self.handler.init_table(self.test_table_def)

        await self.handler.create("test_table", {"name": "list_test_1", "value": "value_1"})
        await self.handler.create("test_table", {"name": "list_test_2", "value": "value_2"})
        await self.handler.create("test_table", {"name": "list_test_3", "value": "value_3"})

        records = await self.handler.list_records("test_table", limit=10)

        self.assertEqual(len(records), 3)

        records_limited = await self.handler.list_records("test_table", limit=2)
        self.assertEqual(len(records_limited), 2)

        records_offset = await self.handler.list_records("test_table", limit=2, offset=1)
        self.assertEqual(len(records_offset), 2)

    async def test_list_records_order_by_string_asc(self):
        """测试列表查询 - order_by 字符串升序"""
        await self.handler.init_table(self.test_table_def)

        await self.handler.create("test_table", {"name": "banana", "value": "b"})
        await self.handler.create("test_table", {"name": "apple", "value": "a"})
        await self.handler.create("test_table", {"name": "cherry", "value": "c"})

        records = await self.handler.list_records("test_table", order_by="name")
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].name, "apple")
        self.assertEqual(records[1].name, "banana")
        self.assertEqual(records[2].name, "cherry")

    async def test_list_records_order_by_string_desc(self):
        """测试列表查询 - order_by 字符串降序"""
        await self.handler.init_table(self.test_table_def)

        await self.handler.create("test_table", {"name": "banana", "value": "b"})
        await self.handler.create("test_table", {"name": "apple", "value": "a"})
        await self.handler.create("test_table", {"name": "cherry", "value": "c"})

        records = await self.handler.list_records("test_table", order_by="name DESC")
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].name, "cherry")
        self.assertEqual(records[1].name, "banana")
        self.assertEqual(records[2].name, "apple")

    async def test_list_records_order_by_string_prefix(self):
        """测试列表查询 - order_by 字符串前缀负号降序"""
        await self.handler.init_table(self.test_table_def)

        await self.handler.create("test_table", {"name": "banana", "value": "b"})
        await self.handler.create("test_table", {"name": "apple", "value": "a"})
        await self.handler.create("test_table", {"name": "cherry", "value": "c"})

        records = await self.handler.list_records("test_table", order_by="-name")
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].name, "cherry")
        self.assertEqual(records[1].name, "banana")
        self.assertEqual(records[2].name, "apple")

    async def test_list_records_order_by_list(self):
        """测试列表查询 - order_by 多字段排序"""
        await self.handler.init_table(self.test_table_def)

        await self.handler.create("test_table", {"name": "apple", "value": "z"})
        await self.handler.create("test_table", {"name": "banana", "value": "a"})
        await self.handler.create("test_table", {"name": "apple", "value": "a"})

        records = await self.handler.list_records(
            "test_table",
            order_by=[("name", False), ("value", True)]
        )
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].name, "apple")
        self.assertEqual(records[0].value, "z")  # value 降序，z > a
        self.assertEqual(records[1].name, "apple")
        self.assertEqual(records[1].value, "a")   # value 次之
        self.assertEqual(records[2].name, "banana")

    async def test_list_records_with_filters_and_order_by(self):
        """测试列表查询 - 带过滤器和排序"""
        await self.handler.init_table(self.test_table_def)

        await self.handler.create("test_table", {"name": "alpha", "value": "10"})
        await self.handler.create("test_table", {"name": "beta", "value": "20"})
        await self.handler.create("test_table", {"name": "gamma", "value": "30"})
        await self.handler.create("test_table", {"name": "delta", "value": "40"})
        await self.handler.create("test_table", {"name": "epsilon", "value": "50"})

        records = await self.handler.list_records(
            "test_table",
            order_by="-value",
            limit=3
        )
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].value, "50")
        self.assertEqual(records[1].value, "40")
        self.assertEqual(records[2].value, "30")
