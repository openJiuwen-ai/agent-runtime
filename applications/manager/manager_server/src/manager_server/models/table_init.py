"""Claw Manager 库表初始化（幂等）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from openjiuwen_runtime.foundation.log import get_logger
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from manager_server.models.application_config_models import (
    AUDIT_LOG_CONFIG_TABLE_DEF,
    _MEMORY_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    CRON_POLICY_TABLE_DEF,
    USER_PREFERENCE_DEFAULT_TABLE_DEF,
)
from manager_server.models.approval_models import APPROVAL_TABLE_DEFINITIONS
from manager_server.models.deployment_models import (
    DEPLOYMENT_DEFAULT_ENV_TABLE_DEF,
    DEPLOYMENT_LOG_TABLE_DEF,
    DEPLOYMENT_TASK_TABLE_DEF,
)
from manager_server.models.quota_models import QUOTA_TABLE_DEFINITIONS
from manager_server.models.authz_models import AUTHZ_TABLE_DEFINITIONS
from manager_server.models.instance_access_models import (
    INSTANCE_ACCESS_TABLE_DEFINITIONS,
    INSTANCE_GRANT_TABLE_DEF,
)
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
    EXPERT_PACKAGE_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_CONTAINER_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    SKILL_PREBUILT_TEMPLATE_TABLE_DEF,
)

_log = get_logger(__name__)

ALL_TABLE_DEFINITIONS = (
    INSTANCE_INFO_TABLE_DEF,
    INSTANCE_LINK_BINDING_TABLE_DEF,
    MANAGER_IDENTITY_TABLE_DEF,
    INSTANCE_ENC_PUBKEY_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    CRON_POLICY_TABLE_DEF,
    USER_PREFERENCE_DEFAULT_TABLE_DEF,
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
    EXPERT_PACKAGE_TEMPLATE_TABLE_DEF,
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
    DEPLOYMENT_TASK_TABLE_DEF,
    DEPLOYMENT_LOG_TABLE_DEF,
    DEPLOYMENT_DEFAULT_ENV_TABLE_DEF,
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


async def _ensure_approval_order_expires_at(handler: DBHandler) -> None:
    """Existing manager databases keep the old approval_order shape.

    foundation.init_table() 只建表与补索引，不会给已有表加列；旧库必须先补列再建索引。
    """

    def upgrade(connection):
        inspector = inspect(connection)
        if not inspector.has_table("approval_order"):
            return
        cols = {col["name"] for col in inspector.get_columns("approval_order")}
        if "expires_at" in cols:
            return
        dialect = connection.dialect.name
        column_type = "DATETIME(3)" if dialect == "mysql" else (
            "TIMESTAMP" if dialect == "postgresql" else "DATETIME"
        )
        quote = connection.dialect.identifier_preparer.quote
        connection.execute(text(
            f"ALTER TABLE {quote('approval_order')} "
            f"ADD COLUMN {quote('expires_at')} {column_type} NULL"
        ))

    for attempt in range(3):
        try:
            async with handler.get_engine().begin() as connection:
                await connection.run_sync(upgrade)
            return
        except DBAPIError as exc:
            dialect = handler.get_engine().dialect.name
            sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            errno = exc.orig.args[0] if exc.orig.args else None
            column_race = (
                (dialect == "postgresql" and sqlstate == "42701")
                or (dialect == "mysql" and errno == 1060)
                or "duplicate column" in str(exc.orig).lower()
            )
            if not column_race or attempt == 2:
                raise


async def _drop_instance_grant_upload_skill_policy(handler: DBHandler) -> None:
    """永久下线上传资格列；重复启动幂等。"""
    if not isinstance(handler, SQLAlchemyHandler):
        return

    def migrate(sync_connection) -> None:
        table_name = INSTANCE_GRANT_TABLE_DEF.table_name
        inspector = inspect(sync_connection)
        if not inspector.has_table(table_name):
            return
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        if "upload_skill_policy" not in columns:
            return
        quote = sync_connection.dialect.identifier_preparer.quote
        sync_connection.execute(
            text(
                f"ALTER TABLE {quote(table_name)} "
                f"DROP COLUMN {quote('upload_skill_policy')}"
            )
        )

    try:
        async with handler.get_engine().begin() as connection:
            await connection.run_sync(migrate)
    except DBAPIError as exc:
        dialect = handler.get_engine().dialect.name
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        errno = exc.orig.args[0] if exc.orig.args else None
        message = str(exc.orig).lower()
        already_dropped = (
            (dialect == "postgresql" and sqlstate == "42703")
            or (dialect == "mysql" and errno == 1091)
            or "no such column" in message
            or "does not exist" in message
        )
        if not already_dropped:
            # 删除失败不中止启动：新代码已不再引用该列。
            _log.warning("drop instance_grant.upload_skill_policy failed: %s", exc)


async def _ensure_user_preference_default_extras_column(handler: DBHandler) -> None:
    """旧库补 ``user_preference_default.extras``。"""

    def upgrade(connection):
        inspector = inspect(connection)
        table_name = USER_PREFERENCE_DEFAULT_TABLE_DEF.table_name
        if not inspector.has_table(table_name):
            return
        cols = {col["name"] for col in inspector.get_columns(table_name)}
        if "extras" in cols:
            return
        quote = connection.dialect.identifier_preparer.quote
        dialect = connection.dialect.name
        if dialect == "postgresql":
            col_type = "JSONB"
        elif dialect == "mysql":
            col_type = "JSON"
        else:
            col_type = "TEXT"
        connection.execute(
            text(
                f"ALTER TABLE {quote(table_name)} "
                f"ADD COLUMN {quote('extras')} {col_type} NULL"
            )
        )

    for attempt in range(3):
        try:
            async with handler.get_engine().begin() as connection:
                await connection.run_sync(upgrade)
            return
        except DBAPIError as exc:
            dialect = handler.get_engine().dialect.name
            sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(
                exc.orig, "pgcode", None
            )
            errno = exc.orig.args[0] if exc.orig.args else None
            column_race = (
                (dialect == "postgresql" and sqlstate == "42701")
                or (dialect == "mysql" and errno == 1060)
                or "duplicate column" in str(exc.orig).lower()
            )
            if not column_race or attempt == 2:
                raise


async def init_all_tables(handler: DBHandler) -> None:
    _ensure_bigint_type_support()
    # 必须在表循环之前补列：init_table 建索引会因缺列失败。
    await _ensure_approval_order_expires_at(handler)
    for table_def in ALL_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
    await _ensure_team_quota_org_column(handler)
    await _migrate_a2a_discovery_settings(handler)
    await _drop_instance_grant_upload_skill_policy(handler)
    await _ensure_user_preference_default_extras_column(handler)
    from manager_server.core.authz import seed_authz_defaults

    await seed_authz_defaults(handler)
