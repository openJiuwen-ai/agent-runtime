from urllib.parse import quote

from .dialects import ensure_async_gaussdb_installed, ensure_gaussdb_dialect_registered
from .sqlalchemy_handler import SQLAlchemyHandler
from ..config import settings


class GaussDBHandler(SQLAlchemyHandler):
    """GaussDB/openGauss 数据库句柄。

    连接串使用自定义的 gaussdb+async_gaussdb 方言名，方言层复用 SQLAlchemy
    PostgreSQL asyncpg 适配器，并将底层 DB-API 替换为 async-gaussdb。
    """

    def __init__(self):
        ensure_async_gaussdb_installed()
        ensure_gaussdb_dialect_registered()
        user = quote(settings.DB_USER or "", safe="")
        password = quote(settings.DB_PASSWORD or "", safe="")
        database_url = (
            f"gaussdb+async_gaussdb://{user}:{password}@"
            f"{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
        )
        super().__init__(database_url)