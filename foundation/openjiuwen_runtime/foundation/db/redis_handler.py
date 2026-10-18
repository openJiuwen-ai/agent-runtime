# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import json
from datetime import datetime
from typing import Optional, Any

import redis.asyncio as redis

from .handler import DBHandler
from .table_def import TableDefinition


class RedisHandler(DBHandler):
    """Redis数据库句柄"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.client: Optional[redis.Redis] = None
        self._table_prefixes: dict[str, str] = {}
        self._table_columns: dict[str, list[str]] = {}
        self._id_counters: dict[str, str] = {}

    async def connect(self) -> None:
        self.client = redis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            decode_responses=True,
        )

    async def disconnect(self) -> None:
        if self.client:
            await self.client.close()

    async def init_table(self, table_def: TableDefinition) -> None:
        """初始化表（Redis 中记录表结构信息）"""
        table_name = table_def.table_name
        self._table_prefixes[table_name] = f"{table_name}:"
        self._table_columns[table_name] = [col.name for col in table_def.columns]
        self._id_counters[table_name] = f"{table_name}:id_counter"

        await self.client.set(f"{table_name}:columns", json.dumps(self._table_columns[table_name]))

    async def _get_next_id(self, table_name: str) -> int:
        return await self.client.incr(self._id_counters[table_name])

    def _serialize_value(self, value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, dict):
            return json.dumps(value)
        elif value is None:
            return ""
        return str(value)

    def _deserialize_value(self, value: str, data_type: str = "string") -> Any:
        if value == "":
            return None
        if data_type == "integer":
            return int(value)
        elif data_type == "json":
            return json.loads(value)
        elif data_type == "datetime":
            return datetime.fromisoformat(value)
        return value

    async def create(self, table_name: str, data: dict) -> dict:
        prefix = self._table_prefixes.get(table_name)
        if not prefix:
            raise ValueError(f"Table {table_name} not initialized")

        record_id = data.get("id") or await self._get_next_id(table_name)
        data["id"] = record_id

        if "created_at" not in data or data["created_at"] is None:
            data["created_at"] = datetime.utcnow().isoformat()
        if "updated_at" not in data or data["updated_at"] is None:
            data["updated_at"] = datetime.utcnow().isoformat()

        primary_key = data.get("deployment_id") or data.get("id")
        key = f"{prefix}{primary_key}"

        mapping = {}
        for col, value in data.items():
            mapping[col] = self._serialize_value(value)

        await self.client.hset(key, mapping=mapping)
        await self.client.rpush(f"{table_name}:list", str(primary_key))

        return data

    async def get(self, table_name: str, filters: dict) -> Optional[dict]:
        prefix = self._table_prefixes.get(table_name)
        if not prefix:
            raise ValueError(f"Table {table_name} not initialized")

        primary_key = filters.get("deployment_id") or filters.get("id")
        if not primary_key:
            return None

        key = f"{prefix}{primary_key}"
        data = await self.client.hgetall(key)

        if not data:
            return None

        return {k: self._deserialize_value(v) for k, v in data.items()}

    async def update(self, table_name: str, filters: dict, data: dict) -> Optional[dict]:
        prefix = self._table_prefixes.get(table_name)
        if not prefix:
            raise ValueError(f"Table {table_name} not initialized")

        primary_key = filters.get("deployment_id") or filters.get("id")
        if not primary_key:
            return None

        key = f"{prefix}{primary_key}"

        existing = await self.client.hgetall(key)
        if not existing:
            return None

        data["updated_at"] = datetime.utcnow().isoformat()

        mapping = {}
        for col, value in data.items():
            mapping[col] = self._serialize_value(value)

        await self.client.hset(key, mapping=mapping)

        return await self.get(table_name, filters)

    async def delete(self, table_name: str, filters: dict) -> bool:
        prefix = self._table_prefixes.get(table_name)
        if not prefix:
            raise ValueError(f"Table {table_name} not initialized")

        primary_key = filters.get("deployment_id") or filters.get("id")
        if not primary_key:
            return False

        key = f"{prefix}{primary_key}"
        result = await self.client.delete(key)
        await self.client.lrem(f"{table_name}:list", 0, str(primary_key))

        return result > 0

    async def list_records(
        self,
        table_name: str,
        filters: Optional[dict] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        prefix = self._table_prefixes.get(table_name)
        if not prefix:
            raise ValueError(f"Table {table_name} not initialized")

        # Filters must be applied before the page window: a raw lrange page can
        # contain no match while later list entries do (SQL backend filters first).
        keys = await self.client.lrange(
            f"{table_name}:list", 0 if filters else offset, -1 if filters else offset + limit - 1
        )
        records = []

        for key in keys:
            record = await self.get(table_name, {"deployment_id": key})
            if record:
                if filters:
                    match = True
                    for k, v in filters.items():
                        if record.get(k) != v:
                            match = False
                            break
                    if match:
                        records.append(record)
                else:
                    records.append(record)

        if filters:
            return records[offset:offset + limit]
        return records

    async def count_records(
        self,
        table_name: str,
        filters: Optional[dict] = None,
    ) -> int:
        """与 ``list_records`` 相同的过滤语义；通过遍历列表键计数（大数据量时慎用）。"""
        records = await self.list_records(table_name, filters, limit=2**31 - 1, offset=0)
        return len(records)
