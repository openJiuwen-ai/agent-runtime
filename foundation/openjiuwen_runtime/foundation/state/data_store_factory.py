# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""运行时状态存储工厂：构建通用 DataStore（DB 权威 + Redis cache）。

供 a2a_service / versatile_adapter 等应用层共用，避免各自复制一份。
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote_plus, unquote_plus

from loguru import logger
from openjiuwen_runtime.foundation.db.table_def import ColumnDefinition, TableDefinition
from openjiuwen_runtime.foundation.state.data_store import DataStore
from openjiuwen_runtime.foundation.state.db_data_store import DbDataStore
from openjiuwen_runtime.foundation.state.cache_backed_data_store import CacheBackedDataStore


def _build_gaussdb_handler(settings):
    from openjiuwen_runtime.foundation.db.dialects import (
        ensure_async_gaussdb_installed,
        ensure_gaussdb_dialect_registered,
    )
    from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler

    ensure_async_gaussdb_installed()
    ensure_gaussdb_dialect_registered()
    user = quote_plus(str(settings.runtime_db_user or ""))
    raw_password = unquote_plus(str(settings.runtime_db_password or ""))
    password = quote_plus(raw_password)
    database_url = (
        f"gaussdb+async_gaussdb://{user}:{password}@"
        f"{settings.runtime_db_host}:{settings.runtime_db_port}/{settings.runtime_db_name}"
    )
    return SQLAlchemyHandler(database_url)


def _build_db_handler(settings):
    db_type = (settings.runtime_db_type or "sqlite").lower()
    if db_type == "postgres":
        from openjiuwen_runtime.foundation.db.postgresql_handler import PostgreSQLHandler
        raw_password = unquote_plus(str(settings.runtime_db_password or ""))
        return PostgreSQLHandler(
            host=settings.runtime_db_host,
            port=settings.runtime_db_port,
            database=settings.runtime_db_name,
            user=settings.runtime_db_user,
            password=raw_password,
        )
    if db_type in {"gaussdb", "opengauss"}:
        return _build_gaussdb_handler(settings)
    if db_type == "sqlite":
        from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
        return SQLiteHandler(db_path=settings.runtime_db_sqlite_path)
    raise ValueError(f"unsupported runtime_db_type: {db_type}")


def _runtime_kv_table_def() -> TableDefinition:
    return TableDefinition(
        table_name="runtime_kv_state",
        columns=[
            ColumnDefinition(name="id", data_type="integer", primary_key=True, autoincrement=True, nullable=False),
            ColumnDefinition(name="state_domain", data_type="string", length=128, nullable=False),
            ColumnDefinition(name="state_key", data_type="string", length=256, nullable=False),
            ColumnDefinition(name="payload", data_type="json", nullable=False),
            ColumnDefinition(name="version", data_type="integer", nullable=False, default=1),
            ColumnDefinition(name="state_metadata", data_type="json", nullable=True),
            ColumnDefinition(name="updated_at", data_type="datetime", nullable=False),
            ColumnDefinition(name="expire_at", data_type="datetime", nullable=True),
        ],
    )


async def build_runtime_state_store_and_db_handler(
    *,
    settings: Any,
    cache_store: Any,
    table_name: str = "runtime_kv_state",
    key_prefix: str = "runtime",
) -> tuple[DataStore, Any]:
    """构建运行时状态存储（DB 权威 + Redis cache）。

    Args:
        settings: 配置对象，需包含 runtime_db_type/host/port/name/user/password 等属性。
        cache_store: Redis 缓存客户端，需实现 get_json / set_json / delete 接口。
        table_name: DB 表名，默认 runtime_kv_state。
        key_prefix: Redis cache key 前缀，默认 runtime。

    Returns:
        (CacheBackedDataStore, db_handler) — 调用方需在关闭时 disconnect db_handler。
    """
    db_type = (settings.runtime_db_type or "sqlite").lower()
    logger.info("[DataStore] 开始构建运行时状态存储：db_type={}, backend=DB权威+Redis缓存", db_type)

    db_handler = _build_db_handler(settings)
    await db_handler.connect()
    logger.info("[DataStore] DB 连接成功：db_type={}", db_type)

    await db_handler.init_table(_runtime_kv_table_def())
    logger.info("[DataStore] 表 {} 初始化完成", table_name)

    db_store = DbDataStore(db_handler, table_name=table_name)
    data_store = CacheBackedDataStore(db_store=db_store, cache_store=cache_store, key_prefix=key_prefix)
    logger.info("[DataStore] CacheBackedDataStore 构建完成：write=先DB后Redis, read=先Redis_miss回DB回填, remove=先DB后Redis")
    return data_store, db_handler
