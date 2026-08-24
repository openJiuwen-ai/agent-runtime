from urllib.parse import quote

from .dialects import ensure_async_gaussdb_installed, ensure_gaussdb_dialect_registered
from .engine_options import get_query_timeout_seconds
from .sqlalchemy_handler import SQLAlchemyHandler
from ..config import settings


class GaussDBHandler(SQLAlchemyHandler):
    """GaussDB/openGauss 数据库句柄。

    连接串使用自定义的 gaussdb+async_gaussdb 方言名，方言层复用 SQLAlchemy
    PostgreSQL asyncpg 适配器，并将底层 DB-API 替换为 async-gaussdb。
    """

    def _prepare_db_timeout_args(self) -> dict:
        """async-gaussdb（asyncpg 协议）：``statement_timeout`` + ``command_timeout``。"""
        timeout = get_query_timeout_seconds()
        if timeout is None:
            return {}
        timeout_ms = max(1, int(timeout * 1000))
        return {
            "command_timeout": timeout,
            "server_settings": {"statement_timeout": str(timeout_ms)},
        }

    def __init__(self):
        ensure_async_gaussdb_installed()
        ensure_gaussdb_dialect_registered()
        user = quote(settings.RUNTIME_DB_USER or "", safe="")
        password = quote(settings.RUNTIME_DB_PASSWORD or "", safe="")
        database_url = (
            f"gaussdb+async_gaussdb://{user}:{password}@"
            f"{settings.RUNTIME_DB_HOST}:{settings.RUNTIME_DB_PORT}/{settings.RUNTIME_DB_NAME}"
        )
        super().__init__(database_url, connect_args=self._prepare_db_timeout_args())