# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .engine_options import get_connect_timeout
from .sqlalchemy_handler import SQLAlchemyHandler
from ..config import settings
from ..log import get_logger

logger = get_logger(__name__)


class MySQLHandler(SQLAlchemyHandler):
    """MySQL数据库句柄

    连接参数可在构造函数中显式传入；未传入的字段使用 ``settings``（环境变量）中的值。
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.host = host if host is not None else settings.RUNTIME_DB_HOST
        self.port = port if port is not None else settings.RUNTIME_DB_PORT
        self.database = database if database is not None else settings.RUNTIME_DB_NAME
        self.user = user if user is not None else settings.RUNTIME_DB_USER
        self.password = password if password is not None else settings.RUNTIME_DB_PASSWORD

        database_url = (
            f"mysql+aiomysql://{quote_plus(self.user or '')}:{quote_plus(self.password or '')}"
            f"@{self.host}:{self.port}/{self.database}"
        )
        super().__init__(database_url)

    async def init_database(self) -> None:
        """初始化 MySQL 库（存在则跳过，不存在则创建）。"""
        database_name = (self.database or "").strip()
        if not database_name:
            logger.warning("No database name configured, skipping init_database")
            return

        quoted_name = "`" + database_name.replace("`", "``") + "`"
        url = make_url(self.database_url)
        server_url = url.set(database="")
        temp_engine = create_async_engine(
            server_url.render_as_string(hide_password=False),
            echo=False,
            connect_args={"connect_timeout": get_connect_timeout()},
        )
        try:
            async with temp_engine.begin() as conn:
                await conn.execute(
                    text(f"CREATE DATABASE IF NOT EXISTS {quoted_name}")
                )
            logger.info("MySQL database ensured: database=%s", database_name)
        finally:
            await temp_engine.dispose()
