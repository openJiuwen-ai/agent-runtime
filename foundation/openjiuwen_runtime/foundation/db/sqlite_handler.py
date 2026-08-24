# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from pathlib import Path

from .engine_options import get_query_timeout_seconds
from .sqlalchemy_handler import SQLAlchemyHandler
from ..log import get_logger

logger = get_logger(__name__)


class SQLiteHandler(SQLAlchemyHandler):
    """SQLite数据库句柄"""

    def _prepare_db_timeout_args(self) -> dict:
        """aiosqlite：等待写锁的秒数，不是语句执行超时；本地库仍作兜底。"""
        timeout = get_query_timeout_seconds()
        if timeout is None:
            return {}
        return {"timeout": timeout}

    def __init__(self, db_path: str = "deployment_service.db"):
        self.db_path = db_path
        database_url = f"sqlite+aiosqlite:///{db_path}"
        super().__init__(database_url, connect_args=self._prepare_db_timeout_args())

    async def init_database(self) -> None:
        """初始化 SQLite 库路径（存在则跳过，不存在则创建目录）。"""
        if not self.db_path or self.db_path == ":memory:":
            return
        path = Path(self.db_path)
        if path.exists():
            logger.debug("SQLite database already exists: path=%s", self.db_path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("SQLite database path prepared: path=%s", self.db_path)
