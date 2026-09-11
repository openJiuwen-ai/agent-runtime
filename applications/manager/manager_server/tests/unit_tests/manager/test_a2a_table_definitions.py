"""A2A table definitions compile for all Manager-supported SQL dialects."""

from __future__ import annotations

import pytest
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.db.table_def import ColumnDefinition, TableDefinition
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


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_value", [None, False, True])
@pytest.mark.parametrize("has_private_network", [False, True])
async def test_legacy_loopback_migration_allows_saving_settings(
    tmp_path, legacy_value, has_private_network,
):
    from manager_server.core.template.a2a_discovery_settings import A2ADiscoverySettingsService
    from manager_server.schemas.template_schemas import A2ADiscoverySettingsBody

    definition = A2A_DISCOVERY_SETTINGS_TABLE_DEF
    assert "allow_loopback" not in {column.name for column in definition.columns}
    handler = SQLiteHandler(str(tmp_path / "legacy.db"))
    await handler.connect()
    try:
        columns = [
            column for column in definition.columns
            if has_private_network or column.name != "allow_private_network"
        ]
        legacy_table = _as_sqlalchemy_table(TableDefinition(
            table_name=definition.table_name,
            columns=[*columns, ColumnDefinition(
                "allow_loopback", "boolean", nullable=False,
            )],
            indexes=definition.indexes,
        ))
        async with handler.engine.begin() as connection:
            await connection.run_sync(legacy_table.metadata.create_all)
        now = utc_now()
        if legacy_value is not None:
            values = {
                "settings_id": "global", "allow_loopback": legacy_value,
                "allow_http": True, "allow_public_http": True,
                "created_at": now, "updated_at": now,
            }
            if has_private_network:
                values["allow_private_network"] = True
            async with handler.engine.begin() as connection:
                await connection.execute(legacy_table.insert().values(**values))
        await init_all_tables(handler)
        await init_all_tables(handler)
        service = A2ADiscoverySettingsService(handler)
        settings = await service.get()
        assert settings.model_dump() == {
            "allow_http": legacy_value is not None,
            "allow_private_network": legacy_value is not None and has_private_network,
            "allow_public_http": legacy_value is not None,
        }
        desired = A2ADiscoverySettingsBody(
            allow_http=True, allow_private_network=True, allow_public_http=False,
        )
        await service.update(desired)
        assert await service.get() == desired
        desired.allow_http = False
        await service.update(desired)
        assert await service.get() == desired
        if legacy_value is not None:
            row = await handler.get(definition.table_name, {"settings_id": "global"})
            assert row.created_at == now.replace(tzinfo=None)
        async with handler.engine.connect() as connection:
            columns = await connection.run_sync(
                lambda conn: {item["name"] for item in inspect(conn).get_columns(definition.table_name)}
            )
            assert "allow_loopback" not in columns
    finally:
        await handler.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("dialect,code,retry", [
    ("postgresql", "42701", True), ("postgresql", "42703", True),
    ("mysql", 1060, True), ("mysql", 1091, True),
    ("postgresql", "42501", False), ("mysql", 1142, False),
])
async def test_migration_retries_column_races_after_rollback(dialect, code, retry):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock
    from sqlalchemy.exc import DBAPIError
    from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
    from manager_server.models.table_init import _migrate_a2a_discovery_settings

    original = Exception(code)
    original.sqlstate = code if dialect == "postgresql" else None
    failure = DBAPIError("ALTER TABLE", {}, original)
    events = []

    class Connection:
        async def run_sync(self, migrate):
            events.append("inspect")
            if events.count("inspect") == 1:
                raise failure

    @asynccontextmanager
    async def begin():
        events.append("begin")
        try:
            yield Connection()
        except DBAPIError:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    handler = Mock(spec=SQLAlchemyHandler)
    handler.engine = SimpleNamespace(begin=begin, dialect=SimpleNamespace(name=dialect))
    if retry:
        await _migrate_a2a_discovery_settings(handler)
        assert events == ["begin", "inspect", "rollback", "begin", "inspect", "commit"]
    else:
        with pytest.raises(DBAPIError) as caught:
            await _migrate_a2a_discovery_settings(handler)
        assert caught.value is failure
        assert events == ["begin", "inspect", "rollback"]
