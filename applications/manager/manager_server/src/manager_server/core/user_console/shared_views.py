"""Initialize the user-plane views and read-only account during Manager startup."""

import asyncio
import hashlib
import os
import re

from openjiuwen_runtime.foundation.db.handler import DBHandler
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from manager_server.infrastructure.db import create_db_handler

_VIEWS = ("user_access_instance", "user_access_instance_grant", "user_access_agent_resource")


async def _create_reader(
    connection: AsyncConnection, username: str, password: str, schema: str | None = None,
) -> None:
    database = connection.engine.url.database
    quote = connection.dialect.identifier_preparer.quote
    if connection.dialect.name in {"mysql", "mariadb"}:
        account = {"name": username, "host": "%", "password": password}
        await connection.execute(text(
            "CREATE USER IF NOT EXISTS :name@:host IDENTIFIED BY :password"
        ), account)
        grants = await connection.execute(text("SHOW GRANTS FOR :name@:host"), account)
        allowed = ("GRANT USAGE ON *.* TO ", *(
            f"GRANT SELECT ON {database}.{view} TO " for view in _VIEWS
        ))
        for row in grants:
            grant = row[0].replace('`', '').replace('"', '')
            if not grant.startswith(allowed) or "WITH GRANT OPTION" in grant:
                raise ValueError("User-plane account already has permissions beyond its shared views")
        for view in _VIEWS:
            await connection.execute(text(
                f"GRANT SELECT ON {quote(database)}.{quote(view)} TO :name@:host"
            ), account)
        return

    roles = await connection.execute(text(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname = :name"
    ), {"name": username})
    role = roles.one_or_none()
    if role is None:
        literal = (await connection.execute(
            text("SELECT quote_literal(CAST(:password AS text))"), {"password": password},
        )).scalar_one()
        await connection.exec_driver_sql(f"CREATE ROLE {quote(username)} LOGIN PASSWORD {literal}")
    elif any(role):
        raise ValueError("User-plane account must not have PostgreSQL administrative privileges")
    member = await connection.execute(text(
        "SELECT 1 FROM pg_auth_members WHERE member = "
        "(SELECT oid FROM pg_roles WHERE rolname = :name)"
    ), {"name": username})
    if member.first():
        raise ValueError("User-plane account must not inherit other database roles")
    schema = schema or "public"
    privileges = await connection.execute(text(
        "SELECT table_schema, table_name, privilege_type, is_grantable "
        "FROM information_schema.table_privileges WHERE grantee = :name"
    ), {"name": username})
    for row in privileges:
        is_shared_view = row.table_schema == schema and row.table_name in _VIEWS
        if (
            not is_shared_view or row.privilege_type != "SELECT"
            or row.is_grantable == "YES"
        ):
            raise ValueError("User-plane account already has permissions beyond its shared views")
    await connection.execute(text(f"GRANT CONNECT ON DATABASE {quote(database)} TO {quote(username)}"))
    await connection.execute(text(f"GRANT USAGE ON SCHEMA {quote(schema)} TO {quote(username)}"))
    for view in _VIEWS:
        await connection.execute(text(
            f"GRANT SELECT ON {quote(schema)}.{quote(view)} TO {quote(username)}"
        ))


async def initialize_user_access(handler: DBHandler) -> None:
    """Complete shared access after the existing Manager tables have been created."""
    username = os.getenv("USER_BACKEND_DB_USER", "")
    if not username:
        return
    password = os.getenv("USER_BACKEND_DB_PASSWORD", "")
    is_dedicated_account = (
        username.lower() not in {"root", "postgres"}
        and username != getattr(handler, "user", None)
    )
    if (
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", username)
        or not is_dedicated_account
        or not password
    ):
        raise ValueError("A separate user-plane account and password are required")
    engine = handler.get_engine()
    schema = getattr(handler, "schema", None)
    if engine.dialect.name not in {"mysql", "mariadb", "postgresql"}:
        raise ValueError("User-plane database initialization requires MySQL/MariaDB or PostgreSQL")
    lock = "user_access_" + hashlib.sha256(f"{engine.url.database}/{username}".encode()).hexdigest()[:40]
    try:
        async with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": lock},
                )
                await create_views(connection, schema)
                await _create_reader(connection, username, password, schema)
            else:
                acquired = await connection.execute(text("SELECT GET_LOCK(:name, 30)"), {"name": lock})
                if acquired.scalar() != 1:
                    raise RuntimeError("Timed out waiting for user-plane database initialization")
                try:
                    await create_views(connection, schema)
                    await _create_reader(connection, username, password, schema)
                finally:
                    await connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock})
        reader = create_async_engine(
            engine.url.set(username=username, password=password), poolclass=NullPool,
            hide_parameters=True,
        )
        try:
            async with reader.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await reader.dispose()
    except SQLAlchemyError:
        # DDL failures must not expose password literals or connection parameters.
        raise RuntimeError(
            "User-plane DB initialization failed; check provisioning privileges and account credentials"
        ) from None


async def create_views(connection: AsyncConnection, schema: str | None = None) -> None:
    """Publish stable, explicitly enumerated projections."""
    dialect = connection.dialect
    mysql_family = dialect.name in {"mysql", "mariadb"}
    quote = dialect.identifier_preparer.quote
    prefix = f"{quote(schema)}." if schema else ""
    if dialect.name == "postgresql":
        http = "data ->> 'gateway_web_http_host'"
        ws = "data ->> 'gateway_web_ws_host'"
    elif mysql_family:
        http = "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(data, '$.gateway_web_http_host')), 'null')"
        ws = "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(data, '$.gateway_web_ws_host')), 'null')"
    else:
        raise ValueError("User backend projections require PostgreSQL or MySQL/MariaDB")
    views = (
        ("user_access_instance", "instance_info",
         f"jiuwenclaw_id, jiuwenclaw_name, {http} AS gateway_http, {ws} AS gateway_ws"),
        ("user_access_instance_grant", "instance_grant",
         "jiuwenclaw_id, subject_type, subject_id, login_policy, enabled, expires_at"),
        ("user_access_agent_resource", "instance_agent_resource",
         "jiuwenclaw_id, resource_id, resource_name, match_expr, enabled, expires_at"),
    )
    for view, table, columns in views:
        security = "SQL SECURITY DEFINER " if mysql_family else ""
        await connection.execute(text(
            f"CREATE OR REPLACE {security}VIEW {prefix}{quote(view)} "
            f"AS SELECT {columns} FROM {prefix}{quote(table)}"
        ))


async def _main() -> None:
    handler = create_db_handler()
    await handler.connect()
    try:
        engine = handler.get_engine()
        async with engine.begin() as connection:
            await create_views(connection, getattr(handler, "schema", None))
    finally:
        await handler.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
