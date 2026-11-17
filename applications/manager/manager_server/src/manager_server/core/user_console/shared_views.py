"""Create the Manager projections before starting a user backend.

Run with Manager's database configuration. Grant the service account SELECT on
each view separately with the database's provisioning credentials.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from manager_server.infrastructure.db import create_db_handler


async def create_views(connection: AsyncConnection, schema: str | None = None) -> None:
    """Publish stable, explicitly enumerated projections."""
    dialect = connection.dialect
    quote = dialect.identifier_preparer.quote
    prefix = f"{quote(schema)}." if schema else ""
    if dialect.name == "postgresql":
        http = "data ->> 'gateway_web_http_host'"
        ws = "data ->> 'gateway_web_ws_host'"
    elif dialect.name == "mysql":
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
        security = "SQL SECURITY DEFINER " if dialect.name == "mysql" else ""
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
