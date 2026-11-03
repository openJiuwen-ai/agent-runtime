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
    CRON_POLICY_TABLE_DEF,
    PACKAGE_ALLOWLIST_TABLE_DEF,
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
from manager_server.models.skill_upload_models import (
    SKILL_UPLOAD_PERMISSION_TABLE_DEF,
    SKILL_UPLOAD_TABLE_DEFINITIONS,
)
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
    CRON_POLICY_TABLE_DEF,
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
    PACKAGE_ALLOWLIST_TABLE_DEF,
    JID_TEMPLATE_REF_TABLE_DEF,
    *INSTANCE_ACCESS_TABLE_DEFINITIONS,
    *INSTANCE_RESOURCE_TABLE_DEFINITIONS,
    *AUTHZ_TABLE_DEFINITIONS,
    *QUOTA_TABLE_DEFINITIONS,
    *APPROVAL_TABLE_DEFINITIONS,
    *SKILL_UPLOAD_TABLE_DEFINITIONS,
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


async def _migrate_skill_upload_permission(handler: DBHandler) -> None:
    """``skill_upload_permission`` 旧表 schema 升级到新 schema。

    覆盖两代 schema 变更：
    1. 早期表无 ``cluster_id`` 列、唯一索引为 ``(subject_type, subject_id)``；
       新 schema 增 ``cluster_id`` 列、唯一索引改为
       ``(cluster_id, subject_type, subject_id)``。
    2. 旧版用 ``status`` 字符串列（active/disabled/revoked）；
       新 schema 按设计 §10.2 改为 ``enabled`` 布尔列。

    ``init_table`` 的 ``extend_existing`` + ``create_all`` 对已存在表只补索引不改列，
    建索引或新列时会因缺列报错导致 manager 启动失败；本函数幂等补列 + 切换索引 +
    数据迁移，重复启动不报错。
    """
    if not isinstance(handler, SQLAlchemyHandler):
        return

    table_name = SKILL_UPLOAD_PERMISSION_TABLE_DEF.table_name
    legacy_index = "ix_skill_upload_permission_subject_type_subject_id"
    new_index = "ix_skill_upload_permission_cluster_id_subject_type_subject_id"

    def migrate(sync_connection) -> None:
        inspector = inspect(sync_connection)
        existing_tables = set(inspector.get_table_names())
        if table_name not in existing_tables:
            # init_table 尚未建表；迁移交给 create_all 完成。
            return
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        quote = sync_connection.dialect.identifier_preparer.quote
        dialect = sync_connection.dialect.name

        # 1. 补 cluster_id 列
        if "cluster_id" not in columns:
            sync_connection.execute(
                text(
                    f"ALTER TABLE {quote(table_name)} ADD COLUMN "
                    f"{quote('cluster_id')} VARCHAR(64) NOT NULL DEFAULT ''"
                )
            )

        # 2. 删除旧唯一索引（仅当列名匹配旧 schema 时）
        existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if legacy_index in existing_indexes:
            if dialect == "mysql":
                sync_connection.execute(
                    text(f"ALTER TABLE {quote(table_name)} DROP INDEX {quote(legacy_index)}")
                )
            else:
                sync_connection.execute(
                    text(f"DROP INDEX {quote(legacy_index)}")
                )

        # 3. 创建新唯一索引（如尚未存在）
        existing_indexes_after = {
            idx["name"] for idx in inspector.get_indexes(table_name)
        }
        if new_index not in existing_indexes_after:
            sync_connection.execute(
                text(
                    f"CREATE UNIQUE INDEX {quote(new_index)} ON {quote(table_name)} "
                    f"({quote('cluster_id')}, {quote('subject_type')}, {quote('subject_id')})"
                )
            )

        # 4. status 列 → enabled 列（设计 §10.2：布尔）
        #    幂等：只在 status 列存在且 enabled 列不存在时迁移数据后删 status。
        columns_after = {item["name"] for item in inspect(sync_connection).get_columns(table_name)}
        if "status" in columns_after and "enabled" not in columns_after:
            # 先加 enabled 列（默认 false，避免 NOT NULL 无默认值）
            if dialect == "mysql":
                sync_connection.execute(
                    text(
                        f"ALTER TABLE {quote(table_name)} ADD COLUMN "
                        f"{quote('enabled')} TINYINT(1) NOT NULL DEFAULT 0"
                    )
                )
            else:
                sync_connection.execute(
                    text(
                        f"ALTER TABLE {quote(table_name)} ADD COLUMN "
                        f"{quote('enabled')} BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
            # 迁移数据：status='active' → enabled=1；其余（disabled/revoked）→ 0
            sync_connection.execute(
                text(
                    f"UPDATE {quote(table_name)} SET {quote('enabled')} = "
                    f"CASE WHEN {quote('status')} = 'active' THEN 1 ELSE 0 END"
                )
            )
            # 删除旧 status 列
            if dialect == "mysql":
                sync_connection.execute(
                    text(f"ALTER TABLE {quote(table_name)} DROP COLUMN {quote('status')}")
                )
            else:
                sync_connection.execute(
                    text(f"ALTER TABLE {quote(table_name)} DROP COLUMN {quote('status')}")
                )

    # 与 _migrate_a2a_discovery_settings 一致：并发实例同时改 schema 时，
    # MySQL/PostgreSQL 会因重复 DDL 报错；捕获 column/index race 重试。
    for attempt in range(3):
        try:
            async with handler.engine.begin() as connection:
                await connection.run_sync(migrate)
            return
        except DBAPIError as exc:
            dialect = handler.engine.dialect.name
            sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            errno = exc.orig.args[0] if exc.orig.args else None
            # MySQL: 1060=dup column, 1091=can't drop missing index, 1061=dup key name
            # PostgreSQL: 42701=dup column, 42703=undefined column, 42P07=dup table
            schema_race = (
                (dialect == "postgresql" and sqlstate in {"42701", "42703", "42P07"})
                or (dialect == "mysql" and errno in {1060, 1061, 1091})
            )
            if not schema_race or attempt == 2:
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


async def _ensure_team_quota_org_column(handler: DBHandler) -> None:
    """Existing manager databases keep the old team_quota_policy shape."""

    def upgrade(connection):
        inspector = inspect(connection)
        if not inspector.has_table("team_quota_policy"):
            return
        cols = {col["name"] for col in inspector.get_columns("team_quota_policy")}
        if "max_teams_per_org" in cols:
            return
        quote = connection.dialect.identifier_preparer.quote
        connection.execute(text(
            f"ALTER TABLE {quote('team_quota_policy')} "
            f"ADD COLUMN {quote('max_teams_per_org')} INTEGER NOT NULL DEFAULT 0"
        ))

    async with handler.get_engine().begin() as connection:
        await connection.run_sync(upgrade)


async def init_all_tables(handler: DBHandler) -> None:
    _ensure_bigint_type_support()
    for table_def in ALL_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
    await _ensure_team_quota_org_column(handler)
    await _migrate_a2a_discovery_settings(handler)
    await _migrate_skill_upload_permission(handler)
    from manager_server.core.authz import seed_authz_defaults

    await seed_authz_defaults(handler)
