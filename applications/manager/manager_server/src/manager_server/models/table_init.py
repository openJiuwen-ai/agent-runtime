"""Claw Manager 库表初始化（幂等）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from manager_server.models.application_config_models import (
    AUDIT_LOG_CONFIG_TABLE_DEF,
    _MEMORY_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
)
from manager_server.models.approval_models import APPROVAL_TABLE_DEFINITIONS
from manager_server.models.quota_models import QUOTA_TABLE_DEFINITIONS
from manager_server.models.authz_models import AUTHZ_TABLE_DEFINITIONS
from manager_server.models.instance_access_models import INSTANCE_ACCESS_TABLE_DEFINITIONS
from manager_server.models.instance_models import INSTANCE_INFO_TABLE_DEF
from manager_server.models.instance_resource_models import INSTANCE_RESOURCE_TABLE_DEFINITIONS
from manager_server.models.jid_template_ref_models import (
    JID_TEMPLATE_REF_TABLE_DEF,
)
from manager_server.models.key_models import (
    INSTANCE_ENC_PUBKEY_TABLE_DEF,
    MANAGER_IDENTITY_TABLE_DEF,
)
from manager_server.models.link_binding_models import INSTANCE_LINK_BINDING_TABLE_DEF
from manager_server.models.template_models import (
    A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
    A2A_DISCOVERY_SETTINGS_TABLE_DEF,
    A2A_OUTBOUND_DISCOVERY_TABLE_DEF,
    A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
    AGENT_TEMPLATE_TABLE_DEF,
    EMBEDDING_TEMPLATE_TABLE_DEF,
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    MCP_TEMPLATE_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
    PERMISSIONS_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_CONTAINER_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    SKILL_PREBUILT_TEMPLATE_TABLE_DEF,
)

ALL_TABLE_DEFINITIONS = (
    INSTANCE_INFO_TABLE_DEF,
    INSTANCE_LINK_BINDING_TABLE_DEF,
    MANAGER_IDENTITY_TABLE_DEF,
    INSTANCE_ENC_PUBKEY_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    _MEMORY_CONFIG_TABLE_DEF,
    AUDIT_LOG_CONFIG_TABLE_DEF,
    A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
    A2A_OUTBOUND_DISCOVERY_TABLE_DEF,
    A2A_DISCOVERY_SETTINGS_TABLE_DEF,
    A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
    EMBEDDING_TEMPLATE_TABLE_DEF,
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    SKILL_PREBUILT_TEMPLATE_TABLE_DEF,
    PERMISSIONS_TEMPLATE_TABLE_DEF,
    MCP_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_CONTAINER_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    AGENT_TEMPLATE_TABLE_DEF,
    JID_TEMPLATE_REF_TABLE_DEF,
    *INSTANCE_ACCESS_TABLE_DEFINITIONS,
    *INSTANCE_RESOURCE_TABLE_DEFINITIONS,
    *AUTHZ_TABLE_DEFINITIONS,
    *QUOTA_TABLE_DEFINITIONS,
    *APPROVAL_TABLE_DEFINITIONS,
)


async def _migrate_a2a_discovery_settings(handler: DBHandler) -> None:
    if not isinstance(handler, SQLAlchemyHandler):
        return

    def migrate(sync_connection) -> None:
        table_name = A2A_DISCOVERY_SETTINGS_TABLE_DEF.table_name
        columns = {item["name"] for item in inspect(sync_connection).get_columns(table_name)}
        quote = sync_connection.dialect.identifier_preparer.quote
        if "allow_private_network" not in columns:
            sync_connection.execute(
                text(
                    f"ALTER TABLE {quote(table_name)} ADD COLUMN "
                    f"{quote('allow_private_network')} BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
        if "allow_loopback" in columns:
            # The retired NOT NULL column can otherwise reject first-time saves.
            sync_connection.execute(
                text(f"ALTER TABLE {quote(table_name)} DROP COLUMN {quote('allow_loopback')}")
            )

    # Reinspect after a competing instance changes either column. Catch outside
    # begin() so PostgreSQL's failed transaction is rolled back before retrying.
    for attempt in range(3):
        try:
            async with handler.engine.begin() as connection:
                await connection.run_sync(migrate)
            return
        except DBAPIError as exc:
            dialect = handler.engine.dialect.name
            sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            errno = exc.orig.args[0] if exc.orig.args else None
            column_race = (
                dialect == "postgresql" and sqlstate in {"42701", "42703"}
            ) or (dialect == "mysql" and errno in {1060, 1091})
            if not column_race or attempt == 2:
                raise


_BIGINT_TYPE_PATCH_APPLIED = False


def _ensure_bigint_type_support() -> None:
    """旧版 foundation 不认识 ``bigint`` 时会落成无长度 VARCHAR。

    MySQL 的 ``create_all`` 在编译阶段失败，事务回滚，新表不会出现。
    通过 getattr/setattr 做兼容补丁，避免直接访问受保护成员触发 G.CLS.11。
    """
    global _BIGINT_TYPE_PATCH_APPLIED
    if _BIGINT_TYPE_PATCH_APPLIED:
        return

    from sqlalchemy import BigInteger, String
    from openjiuwen_runtime.foundation.db.table_def import ColumnDefinition

    getter = getattr(SQLAlchemyHandler, "_get_sqlalchemy_type", None)
    sql_getter = getattr(SQLAlchemyHandler, "_get_column_sql_type", None)
    if getter is None or sql_getter is None:
        _BIGINT_TYPE_PATCH_APPLIED = True
        return

    probe = object.__new__(SQLAlchemyHandler)
    try:
        sa_type = getter(probe, "bigint")
        sql_type = str(
            sql_getter(probe, ColumnDefinition("x", "bigint", nullable=False))
        ).upper()
    except Exception:  # noqa: BLE001 - probe may fail on incomplete handler stubs
        sa_type = None
        sql_type = ""

    # 现行 foundation 已原生支持 bigint，无需再打补丁。
    maps_to_string = sa_type is String or isinstance(sa_type, String)
    if sql_type == "BIGINT" and sa_type is not None and not maps_to_string:
        _BIGINT_TYPE_PATCH_APPLIED = True
        return

    def _get_sqlalchemy_type(self, data_type: str, length=None):
        if str(data_type or "").lower() == "bigint":
            return BigInteger
        return getter(self, data_type, length)

    def _get_column_sql_type(self, col_def):
        if str(getattr(col_def, "data_type", "") or "").lower() == "bigint":
            return "BIGINT"
        return sql_getter(self, col_def)

    setattr(SQLAlchemyHandler, "_get_sqlalchemy_type", _get_sqlalchemy_type)
    setattr(SQLAlchemyHandler, "_get_column_sql_type", _get_column_sql_type)
    _BIGINT_TYPE_PATCH_APPLIED = True


async def init_all_tables(handler: DBHandler) -> None:
    _ensure_bigint_type_support()
    for table_def in ALL_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
    await _migrate_a2a_discovery_settings(handler)
    from manager_server.core.authz import seed_authz_defaults

    await seed_authz_defaults(handler)
