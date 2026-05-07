from __future__ import annotations

from importlib import import_module

from sqlalchemy.dialects import registry
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.util import memoized_property

_MODULE_PATH = "openjiuwen_runtime.foundation.db.dialects.gaussdb_asyncgaussdb"
_DRIVER_INSTALL_HINT = (
    "DB_TYPE is set to gaussdb/opengauss, but optional dependency async-gaussdb is not installed. "
    "Install openjiuwen-runtime-foundation[gaussdb] or async-gaussdb>=0.30.4."
)


def _import_async_gaussdb():
    try:
        return import_module("async_gaussdb")
    except ModuleNotFoundError as exc:
        if exc.name != "async_gaussdb":
            raise
        raise ModuleNotFoundError(_DRIVER_INSTALL_HINT) from exc


def _import_split_server_version_string():
    try:
        module = import_module("async_gaussdb.serverversion")
    except ModuleNotFoundError as exc:
        if exc.name not in {"async_gaussdb", "async_gaussdb.serverversion"}:
            raise
        raise ModuleNotFoundError(_DRIVER_INSTALL_HINT) from exc
    return module.split_server_version_string


def ensure_async_gaussdb_installed() -> None:
    _import_async_gaussdb()


class AsyncAdapt_async_gaussdb_dbapi(AsyncAdapt_asyncpg_dbapi):
    @memoized_property
    def _asyncpg_error_translate(self):
        exceptions = self.asyncpg.exceptions
        mappings = {
            getattr(exceptions, "IntegrityConstraintViolationError", None): self.IntegrityError,
            getattr(exceptions, "PostgresError", None): self.Error,
            getattr(exceptions, "SyntaxOrAccessError", None): self.ProgrammingError,
            getattr(exceptions, "InterfaceError", None): self.InterfaceError,
            getattr(exceptions, "InvalidCachedStatementError", None): self.InvalidCachedStatementError,
            getattr(exceptions, "InternalServerError", None): self.InternalServerError,
        }
        return {source: target for source, target in mappings.items() if source is not None}


class PGDialect_async_gaussdb(PGDialect_asyncpg):
    driver = "async_gaussdb"
    supports_statement_cache = False

    @classmethod
    def import_dbapi(cls):
        return AsyncAdapt_async_gaussdb_dbapi(_import_async_gaussdb())

    def _get_server_version_info(self, connection):
        version_string = connection.exec_driver_sql("select pg_catalog.version()").scalar()
        split_server_version_string = _import_split_server_version_string()
        version = split_server_version_string(version_string)
        major = version.major
        # GaussDB kernel versions are not PostgreSQL major versions. If we expose
        # values like 505.x to SQLAlchemy, it enables reflection SQL that expects
        # pg_catalog columns (e.g. pg_attribute.attgenerated) unavailable on some
        # GaussDB/openGauss variants. Advertise a conservative PG compatibility level.
        if major is not None and major >= 100:
            return (11, 0)
        return tuple(part for part in (version.major, version.minor, version.micro) if part is not None)


dialect = PGDialect_async_gaussdb


def ensure_gaussdb_dialect_registered() -> None:
    registry.register("gaussdb.async_gaussdb", _MODULE_PATH, "PGDialect_async_gaussdb")
    registry.register("opengauss.async_gaussdb", _MODULE_PATH, "PGDialect_async_gaussdb")
    registry.register("postgresql.async_gaussdb", _MODULE_PATH, "PGDialect_async_gaussdb")