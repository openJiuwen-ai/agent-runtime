"""A2A table definitions compile for all Manager-supported SQL dialects."""

from __future__ import annotations

import pytest
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.db.table_def import TableDefinition
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    inspect,
)
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable, Index

from manager_server.models.template_models import (
    A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
    A2A_DISCOVERY_SETTINGS_TABLE_DEF,
    A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
    A2A_OUTBOUND_DISCOVERY_TABLE_DEF,
)
from manager_server.models.table_init import init_all_tables
from manager_server.infrastructure.utils import utc_now

pytestmark = pytest.mark.unit

_TYPES = {
    "integer": Integer,
    "string": String,
    "datetime": DateTime,
    "json": JSON,
    "boolean": Boolean,
    "float": Float,
}


def _as_sqlalchemy_table(definition) -> Table:
    metadata = MetaData()
    columns = []
    for item in definition.columns:
        type_class = _TYPES[item.data_type]
        column_type = (
            type_class(item.length) if item.data_type == "string" and item.length else type_class
        )
        columns.append(
            Column(
                item.name,
                column_type,
                primary_key=item.primary_key,
                nullable=item.nullable,
                autoincrement=item.autoincrement,
            )
        )
    table = Table(definition.table_name, metadata, *columns)
    for item in definition.indexes:
        Index(
            item.name or f"ix_{definition.table_name}_{'_'.join(item.columns)}",
            *(table.c[name] for name in item.columns),
            unique=item.unique,
        )
    return table


@pytest.mark.parametrize("dialect", [sqlite.dialect(), mysql.dialect(), postgresql.dialect()])
@pytest.mark.parametrize(
    "definition",
    [
        A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
        A2A_OUTBOUND_DISCOVERY_TABLE_DEF,
        A2A_DISCOVERY_SETTINGS_TABLE_DEF,
        A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
    ],
)
def test_a2a_table_definition_compiles_for_supported_dialects(definition, dialect):
    table = _as_sqlalchemy_table(definition)
    assert str(CreateTable(table).compile(dialect=dialect))
    for index in table.indexes:
        assert str(CreateIndex(index).compile(dialect=dialect))


@pytest.mark.asyncio
async def test_existing_discovery_settings_table_gets_private_network_column(tmp_path):
    handler = SQLiteHandler(str(tmp_path / "manager.db"))
    await handler.connect()
    try:
        old_definition = TableDefinition(
            table_name=A2A_DISCOVERY_SETTINGS_TABLE_DEF.table_name,
            columns=[
                column
                for column in A2A_DISCOVERY_SETTINGS_TABLE_DEF.columns
                if column.name != "allow_private_network"
            ],
            indexes=A2A_DISCOVERY_SETTINGS_TABLE_DEF.indexes,
        )
        await handler.init_table(old_definition)
        now = utc_now()
        await handler.create(
            old_definition.table_name,
            {
                "settings_id": "global",
                "allow_http": True,
                "allow_loopback": True,
                "allow_public_http": True,
                "created_at": now,
                "updated_at": now,
            },
        )
        await init_all_tables(handler)

        async with handler.engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        A2A_DISCOVERY_SETTINGS_TABLE_DEF.table_name
                    )
                }
            )
        assert "allow_private_network" in columns
        migrated = await handler.get(old_definition.table_name, {"settings_id": "global"})
        assert migrated.allow_private_network is False
        assert migrated.allow_http is True
    finally:
        await handler.disconnect()
