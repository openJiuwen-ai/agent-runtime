# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from datetime import datetime
import logging
from typing import Optional, Any
import json
from sqlalchemy import Column, Integer, String, DateTime, JSON, Boolean, create_engine, text, inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import select, update, delete

from ..log import get_logger
from .handler import DBHandler
from .table_def import TableDefinition, ColumnDefinition

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


class GenericRecord:
    """通用记录类，用于动态表操作"""
    pass


class SQLAlchemyHandler(DBHandler):
    """SQLAlchemy基类实现"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = None
        self.session_factory = None
        self._table_models: dict[str, Any] = {}
        logger.debug("SQLAlchemyHandler created")

    def is_table_registered(self, table_name: str) -> bool:
        """是否已通过 init_table 注册过对应 ORM 模型（供测试等场景使用）。"""
        return table_name in self._table_models

    async def connect(self) -> None:
        logger.info("Connecting to database")
        # 关闭 aiosqlite 的 DEBUG 日志
        logging.getLogger("aiosqlite").setLevel(logging.WARNING)
        self.engine = create_async_engine(self.database_url, echo=False)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        logger.info("Database connected")

    async def disconnect(self) -> None:
        logger.info("Disconnecting from database")
        if self.engine:
            await self.engine.dispose()
        logger.info("Database disconnected")

    def _get_sqlalchemy_type(self, data_type: str, length: Optional[int] = None):
        """将数据类型字符串转换为 SQLAlchemy 类型"""
        type_map = {
            "integer": Integer,
            "int": Integer,
            "string": String,
            "str": String,
            "text": String,
            "datetime": DateTime,
            "json": JSON,
            "boolean": Boolean,
            "bool": Boolean,
        }
        sa_type = type_map.get(data_type.lower(), String)
        if sa_type == String and length:
            return String(length)
        return sa_type

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return f'"{identifier}"'

    def _get_column_sql_type(self, col_def: ColumnDefinition) -> str:
        data_type = col_def.data_type.lower()
        if data_type in {"integer", "int"}:
            return "INTEGER"
        if data_type in {"string", "str", "text"}:
            if col_def.length:
                return f"VARCHAR({col_def.length})"
            return "VARCHAR"
        if data_type == "datetime":
            return "DATETIME"
        if data_type == "json":
            return "JSON"
        if data_type in {"boolean", "bool"}:
            return "BOOLEAN"
        return "VARCHAR"

    @staticmethod
    def _format_default_value(value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, datetime):
            return f"'{value.isoformat(sep=' ')}'"
        if isinstance(value, (dict, list)):
            escaped = json.dumps(value).replace("'", "''")
            return f"'{escaped}'"
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    def _build_add_column_sql(self, table_name: str, col_def: ColumnDefinition) -> str:
        if col_def.primary_key or col_def.unique or col_def.autoincrement:
            raise RuntimeError(
                f"Cannot auto-migrate constrained column {table_name}.{col_def.name}"
            )
        if not col_def.nullable and col_def.default is None:
            raise RuntimeError(
                f"Cannot auto-migrate non-null column without default: {table_name}.{col_def.name}"
            )

        column_sql = [
            self._quote_identifier(col_def.name),
            self._get_column_sql_type(col_def),
        ]
        if not col_def.nullable:
            column_sql.append("NOT NULL")
        if col_def.default is not None:
            column_sql.append(f"DEFAULT {self._format_default_value(col_def.default)}")

        return (
            f"ALTER TABLE {self._quote_identifier(table_name)} "
            f"ADD COLUMN {' '.join(column_sql)}"
        )

    def _sync_missing_columns(self, sync_conn, table_def: TableDefinition) -> None:
        logger.warning(f"_sync_missing_columns {table_def.table_name}")
        inspector = inspect(sync_conn)
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_def.table_name)
        }
        missing_columns = [
            col_def for col_def in table_def.columns if col_def.name not in existing_columns
        ]

        for col_def in missing_columns:
            alter_sql = self._build_add_column_sql(table_def.table_name, col_def)
            sync_conn.execute(text(alter_sql))
            logger.warning(
                "Added missing column during table init: table=%s, column=%s",
                table_def.table_name,
                col_def.name,
            )

    async def init_table(self, table_def: TableDefinition) -> None:
        """初始化表（存在则跳过，不存在则创建）"""
        logger.debug("Initializing table: table_name=%s", table_def.table_name)
        columns = []
        for col_def in table_def.columns:
            sa_type = self._get_sqlalchemy_type(col_def.data_type, col_def.length)
            col_kwargs = {
                "primary_key": col_def.primary_key,
                "nullable": col_def.nullable,
                "unique": col_def.unique,
                "autoincrement": col_def.autoincrement,
            }
            if col_def.default is not None:
                col_kwargs["default"] = col_def.default
            if sa_type == String and col_def.length:
                col = Column(sa_type(col_def.length), **col_kwargs)
            else:
                col = Column(sa_type, **col_kwargs)
            columns.append(col)

        table = type(
            table_def.table_name.capitalize() + "Record",
            (Base,),
            {
                "__tablename__": table_def.table_name,
                "__table_args__": {"extend_existing": True},
                **{col_def.name: col for col_def, col in zip(table_def.columns, columns)},
                "to_dict": lambda self: {
                    col.name: getattr(self, col.name) for col in table_def.columns
                },
            },
        )

        self._table_models[table_def.table_name] = table

        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=[table.__table__]
                )
            )
        logger.debug("Table initialized: table_name=%s", table_def.table_name)

    async def _get_session(self) -> AsyncSession:
        return self.session_factory()

    async def create(self, table_name: str, data: dict) -> Any:
        logger.debug("Creating record: table=%s", table_name)
        model = self._table_models.get(table_name)
        if not model:
            logger.error("Table not initialized: table=%s", table_name)
            raise ValueError(f"Table {table_name} not initialized")

        async with await self._get_session() as session:
            record = model(**data)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            logger.debug("Record created: table=%s", table_name)
            return record

    async def get(self, table_name: str, filters: dict) -> Optional[Any]:
        logger.debug("Getting record: table=%s, filters=%s", table_name, filters)
        model = self._table_models.get(table_name)
        if not model:
            logger.error("Table not initialized: table=%s", table_name)
            raise ValueError(f"Table {table_name} not initialized")

        async with await self._get_session() as session:
            query = select(model)
            for key, value in filters.items():
                query = query.where(getattr(model, key) == value)
            result = await session.execute(query)
            record = result.scalar_one_or_none()
            logger.debug("Record found: table=%s, found=%s", table_name, record is not None)
            return record

    async def update(self, table_name: str, filters: dict, data: dict) -> Optional[Any]:
        logger.debug("Updating record: table=%s, filters=%s", table_name, filters)
        model = self._table_models.get(table_name)
        if not model:
            logger.error("Table not initialized: table=%s", table_name)
            raise ValueError(f"Table {table_name} not initialized")

        async with await self._get_session() as session:
            query = update(model)
            for key, value in filters.items():
                query = query.where(getattr(model, key) == value)
            query = query.values(**data)
            await session.execute(query)
            await session.commit()
            logger.debug("Record updated: table=%s", table_name)
            return await self.get(table_name, filters)

    async def delete(self, table_name: str, filters: dict) -> bool:
        logger.debug("Deleting record: table=%s, filters=%s", table_name, filters)
        model = self._table_models.get(table_name)
        if not model:
            logger.error("Table not initialized: table=%s", table_name)
            raise ValueError(f"Table {table_name} not initialized")

        async with await self._get_session() as session:
            query = delete(model)
            for key, value in filters.items():
                query = query.where(getattr(model, key) == value)
            result = await session.execute(query)
            await session.commit()
            deleted = result.rowcount > 0
            logger.debug("Record deleted: table=%s, deleted=%s", table_name, deleted)
            return deleted

    async def list_records(
        self,
        table_name: str,
        filters: Optional[dict] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        logger.debug(
            "Listing records: table=%s, filters=%s, limit=%s, offset=%s",
            table_name,
            filters,
            limit,
            offset,
        )
        model = self._table_models.get(table_name)
        if not model:
            logger.error("Table not initialized: table=%s", table_name)
            raise ValueError(f"Table {table_name} not initialized")

        async with await self._get_session() as session:
            query = select(model)
            if filters:
                for key, value in filters.items():
                    query = query.where(getattr(model, key) == value)
            query = query.offset(offset).limit(limit)
            result = await session.execute(query)
            records = list(result.scalars().all())
            logger.debug("Records listed: table=%s, count=%s", table_name, len(records))
            return records
