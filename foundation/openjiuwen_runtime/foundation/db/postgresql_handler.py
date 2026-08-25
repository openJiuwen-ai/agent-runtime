# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""PostgreSQL 数据库句柄，继承 SQLAlchemyHandler，使用 asyncpg 驱动。"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy import DateTime, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .sqlalchemy_handler import SQLAlchemyHandler
from .table_def import ColumnDefinition
from .engine_options import get_command_timeout, get_connect_timeout
from ..log import get_logger

logger = get_logger(__name__)


class PostgreSQLHandler(SQLAlchemyHandler):
    """PostgreSQL 数据库句柄。

    连接参数通过构造函数显式传入，拼装 ``postgresql+asyncpg://`` URL
    后委托给 :class:`SQLAlchemyHandler` 基类。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5432,
        database: str = "claw_manager",
        schema: str = "public",
        user: str = "postgres",
        password: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.schema = schema
        database_url = (
            f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/{database}"
        )

        connect_args = {}
        if schema and schema.lower() != "public":
            connect_args["server_settings"] = {
                "search_path": schema
            }

        super().__init__(database_url, connect_args=connect_args)

    async def init_database(self) -> None:
        self.database = (self.database or "").strip()
        if not self.database:
            logger.warning("No database name configured, skipping init_database")
            return

        # 确保数据库存在
        await self._ensure_pg_db()

        # 确保schema存在
        await self._ensure_pg_schema()

    async def _ensure_pg_db(self) -> None:
        """确保目标数据库存在（不存在则创建）。
        PostgreSQL 不支持 ``CREATE DATABASE IF NOT EXISTS``，
        因此先连 ``postgres`` 默认库查询 ``pg_database``。
        注意 CREATE DATABASE 不能在事务中执行，需 AUTOCOMMIT。
        """
        # 连接系统默认postgres库（建连/命令超时与主引擎同一兜底）
        url = make_url(self.database_url)
        server_url = url.set(database="postgres")
        temp_engine = create_async_engine(
            server_url.render_as_string(hide_password=False),
            echo=False,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "timeout": get_connect_timeout(),
                "command_timeout": get_command_timeout(),
            },
        )
        try:
            async with temp_engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": self.database},
                )
                if result.scalar() is None:
                    quoted = self.database.replace('"', '""')
                    await conn.execute(text(f'CREATE DATABASE "{quoted}"'))
                    logger.info("PostgreSQL database created: database=%s", self.database)
                else:
                    logger.debug("PostgreSQL database already exists: database=%s", self.database)
        finally:
            await temp_engine.dispose()

    async def _ensure_pg_schema(self) -> None:
        if not self.schema or self.schema.lower() == "public":
            return

        url = make_url(self.database_url)
        server_url = url.set(database=self.database)
        temp_engine = create_async_engine(
            server_url.render_as_string(hide_password=False),
            echo=False,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "timeout": get_connect_timeout(),
                "command_timeout": get_command_timeout(),
            },
        )
        try:
            async with temp_engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.schemata "
                        "WHERE schema_name = :schema"
                    ),
                    {"schema": self.schema},
                )
                if result.scalar() is None:
                    # 安全转义双引号，规避标识符注入
                    quoted_schema = self.schema.replace('"', '""')
                    await conn.execute(text(f'CREATE SCHEMA "{quoted_schema}"'))
                    logger.info("PostgreSQL schema created: schema=%s", self.schema)
                else:
                    logger.debug("PostgreSQL schema already exists: schema=%s", self.schema)
        finally:
            await temp_engine.dispose()

    def _get_sqlalchemy_type(self, data_type: str, length: Optional[int] = None):
        """PostgreSQL 方言类型映射：datetime 使用 TIMESTAMP WITH TIME ZONE。"""
        if data_type.lower() == "datetime":
            return DateTime(timezone=True)
        return super()._get_sqlalchemy_type(data_type, length)

    def _get_column_sql_type(self, col_def: ColumnDefinition) -> str:
        """PostgreSQL 方言类型映射。

        基类将 datetime 映射为 DATETIME（MySQL 语法），PG 需改为 TIMESTAMP。
        此方法仅在 ALTER TABLE ADD COLUMN（增量同步缺失列）时调用。
        """
        if col_def.data_type.lower() == "datetime":
            return "TIMESTAMP WITH TIME ZONE"
        return super()._get_column_sql_type(col_def)

