# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from datetime import datetime
import logging
from typing import Optional, Any
import json
from sqlalchemy import Column, Integer, String, DateTime, JSON, Boolean, Float, Text, text, inspect, Index
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import select, update, delete, func

from ..log import get_logger
from .engine_options import (
    build_async_engine_kwargs,
    get_command_timeout,
    get_connect_timeout,
)
from .handler import DBHandler
from .table_def import TableDefinition, ColumnDefinition, IndexDefinition

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


class GenericRecord:
    """通用记录类，用于动态表操作"""
    pass


class SQLAlchemyHandler(DBHandler):
    """SQLAlchemy基类实现"""

    def __init__(self, database_url: str, connect_args: dict = None):
        self.database_url = database_url
        self.connect_args = connect_args or {}
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
        database_url = make_url(self.database_url)
        connect_args = dict(self.connect_args or {})
        if database_url.get_backend_name() == "mysql":
            # aiomysql 默认建连无超时，网络黑洞时 connect 永久挂起；
            # 调用方显式传值优先（setdefault）。
            connect_args.setdefault("connect_timeout", get_connect_timeout())
        elif database_url.get_driver_name() == "asyncpg":
            # asyncpg：建连 timeout 默认 60s、command_timeout 默认无限制——
            # 均与 MySQL 兜底对齐收紧；调用方显式传值优先（setdefault）。
            connect_args.setdefault("timeout", get_connect_timeout())
            connect_args.setdefault("command_timeout", get_command_timeout())
        engine_kwargs = build_async_engine_kwargs(connect_args=connect_args)
        if (
            database_url.get_backend_name() == "sqlite"
            and database_url.database in {None, "", ":memory:"}
        ):
            # SQLAlchemy selects StaticPool for an in-memory SQLite database.
            # QueuePool-only options are invalid for that pool implementation.
            for option in ("pool_size", "max_overflow", "pool_timeout"):
                engine_kwargs.pop(option, None)
        self.engine = create_async_engine(self.database_url, **engine_kwargs)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        logger.info(
            "Database connected (pool_size=%s max_overflow=%s pool_timeout=%s)",
            engine_kwargs.get("pool_size", "default"),
            engine_kwargs.get("max_overflow", "default"),
            engine_kwargs.get("pool_timeout", "default"),
        )

    async def disconnect(self) -> None:
        logger.info("Disconnecting from database")
        if self.engine:
            await self.engine.dispose()
        logger.info("Database disconnected")

    def get_engine(self) -> Any:
        """获取 SQLAlchemy AsyncEngine 实例."""
        return self.engine

    def get_table(self, table_name: str) -> Any:
        """Return the registered SQLAlchemy table for transactional operations.

        High-level ``DBHandler`` CRUD methods intentionally own their sessions and
        commits.  Applications that need several writes in one transaction can use
        this table together with the handler's ``session_factory``.
        """
        model = self._table_models.get(table_name)
        if model is None:
            raise ValueError(f"Table {table_name} not initialized")
        return model.__table__

    def _get_sqlalchemy_type(self, data_type: str, length: Optional[int] = None):
        """将数据类型字符串转换为 SQLAlchemy 类型"""
        key = data_type.lower()
        # 大文本族：mysql 用 MEDIUMTEXT/LONGTEXT 变体（分别 16MB / 4GB），
        # 其余方言（pg/sqlite/...）用 Text（pg TEXT 无界、sqlite TEXT 无界）。
        if key in ("mediumtext", "longtext"):
            if self._get_dialect_name() == "mysql":
                from sqlalchemy.dialects.mysql import MEDIUMTEXT, LONGTEXT

                return LONGTEXT if key == "longtext" else MEDIUMTEXT
            return Text
        type_map = {
            "integer": Integer,
            "int": Integer,
            "string": String,
            "str": String,
            "text": Text,
            "datetime": DateTime,
            "json": JSON,
            "boolean": Boolean,
            "bool": Boolean,
            "float": Float,
            "double": Float,
            "decimal": Float,
            "number": Float,
            "real": Float,
        }
        sa_type = type_map.get(key, String)
        if sa_type == String and length:
            return String(length)
        return sa_type

    def _get_dialect_name(self) -> str:
        if self.engine is not None:
            return self.engine.dialect.name
        return make_url(self.database_url).get_backend_name()

    def _quote_identifier(self, identifier: str) -> str:
        if self._get_dialect_name() == "mysql":
            return "`" + identifier.replace("`", "``") + "`"
        escaped = identifier.replace('"', '""')
        return f'"{escaped}"'

    def _get_column_sql_type(self, col_def: ColumnDefinition) -> str:
        data_type = col_def.data_type.lower()
        if data_type in {"integer", "int"}:
            return "INTEGER"
        if data_type in {"string", "str"}:
            if col_def.length:
                return f"VARCHAR({col_def.length})"
            return "VARCHAR"
        if data_type in {"mediumtext", "longtext"}:
            if self._get_dialect_name() == "mysql":
                return "LONGTEXT" if data_type == "longtext" else "MEDIUMTEXT"
            return "TEXT"
        if data_type == "text":
            return "TEXT"
        if data_type == "datetime":
            return "DATETIME"
        if data_type == "json":
            return "JSON"
        if data_type in {"boolean", "bool"}:
            return "BOOLEAN"
        if data_type in {"float", "double", "decimal", "number", "real"}:
            return "FLOAT"
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

    def _build_index_name(self, table_name: str, idx_def: IndexDefinition) -> str:
        if idx_def.name:
            return idx_def.name
        return f"ix_{table_name}_{'_'.join(idx_def.columns)}"

    def _create_table_indexes(self, sync_conn, table_def: TableDefinition) -> None:
        """创建缺失索引，不重复创建名称不同但定义等价的索引。"""
        inspector = inspect(sync_conn)
        inspected_indexes = inspector.get_indexes(table_def.table_name)
        existing_names = {
            idx["name"] for idx in inspected_indexes if idx.get("name")
        }
        existing_signatures = {
            (
                tuple(idx.get("column_names") or ()),
                bool(idx.get("unique", False)),
            )
            for idx in inspected_indexes
        }
        table = self._table_models[table_def.table_name].__table__
        for idx_def in table_def.indexes:
            idx_name = self._build_index_name(table_def.table_name, idx_def)
            idx_signature = (tuple(idx_def.columns), idx_def.unique)
            if (
                idx_name in existing_names
                or idx_signature in existing_signatures
            ):
                continue
            index = Index(
                idx_name,
                *[table.c[col] for col in idx_def.columns],
                unique=idx_def.unique,
            )
            index.create(sync_conn)
            logger.debug(
                "Created index during table init: table=%s, index=%s",
                table_def.table_name,
                idx_name,
            )
            existing_names.add(idx_name)
            existing_signatures.add(idx_signature)

    async def init_table(self, table_def: TableDefinition) -> None:
        """初始化表，并幂等补齐声明但尚不存在的索引。"""
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
            def init_sync(sync_conn):
                Base.metadata.create_all(
                    sync_conn, tables=[table.__table__]
                )
                self._create_table_indexes(sync_conn, table_def)

            await conn.run_sync(init_sync)
        logger.debug("Table initialized: table_name=%s", table_def.table_name)

    async def _get_session(self) -> AsyncSession:
        return self.session_factory()

    @staticmethod
    def _filter_condition(column: Any, value: Any) -> Any:
        """Build a WHERE clause.

        Scalar values use equality; list/tuple/set values use SQL ``IN (...)``.
        """
        if isinstance(value, (list, tuple, set)):
            return column.in_(list(value))
        return column == value

    def _apply_filters(self, query: Any, model: Any, filters: dict) -> Any:
        for key, value in filters.items():
            query = query.where(
                self._filter_condition(getattr(model, key), value)
            )
        return query

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
            query = self._apply_filters(select(model), model, filters)
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
            query = self._apply_filters(update(model), model, filters)
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
            query = self._apply_filters(delete(model), model, filters)
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
        order_by: Optional[list[tuple[str, bool]] | str] = None,
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
                query = self._apply_filters(query, model, filters)

            # 处理排序
            if order_by:
                if isinstance(order_by, str):
                    order_parts = order_by.split()
                    field = order_parts[0].lstrip('-')
                    is_desc = len(order_parts) > 1 and order_parts[1].upper() == "DESC"
                    is_desc = is_desc or order_by.startswith('-')
                    query = query.order_by(
                        getattr(model, field).desc() if is_desc else getattr(model, field)
                    )
                elif isinstance(order_by, list):
                    for field, is_desc in order_by:
                        query = query.order_by(
                            getattr(model, field).desc() if is_desc else getattr(model, field)
                        )
            
            query = query.offset(offset).limit(limit)
            result = await session.execute(query)
            records = list(result.scalars().all())
            logger.debug("Records listed: table=%s, count=%s", table_name, len(records))
            return records

    async def count_records(
        self,
        table_name: str,
        filters: Optional[dict] = None,
    ) -> int:
        logger.debug(
            "Counting records: table=%s, filters=%s",
            table_name,
            filters,
        )
        model = self._table_models.get(table_name)
        if not model:
            logger.error("Table not initialized: table=%s", table_name)
            raise ValueError(f"Table {table_name} not initialized")

        stmt = select(func.count()).select_from(model)
        if filters:
            stmt = self._apply_filters(stmt, model, filters)

        async with await self._get_session() as session:
            result = await session.execute(stmt)
            n = int(result.scalar_one())
            logger.debug("Records counted: table=%s, count=%s", table_name, n)
            return n
