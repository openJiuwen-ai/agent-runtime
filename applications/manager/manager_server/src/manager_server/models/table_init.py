"""Claw Manager 库表初始化（幂等）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from sqlalchemy import inspect, text

from manager_server.models.instance_models import INSTANCE_INFO_TABLE_DEF
from manager_server.models.key_models import (
    INSTANCE_ENC_PUBKEY_TABLE_DEF,
    MANAGER_IDENTITY_TABLE_DEF,
)
from manager_server.models.application_config_models import (
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    _MEMORY_CONFIG_TABLE_DEF,
)
from manager_server.models.jid_template_ref_models import (
    JID_TEMPLATE_REF_TABLE_DEF,
)
from manager_server.models.instance_access_models import INSTANCE_ACCESS_TABLE_DEFINITIONS
from manager_server.models.instance_resource_models import INSTANCE_RESOURCE_TABLE_DEFINITIONS
from manager_server.models.template_models import (
    A2A_ACCESS_POLICY_TEMPLATE_TABLE_DEF,
    A2A_DISCOVERY_SETTINGS_TABLE_DEF,
    A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
    A2A_OUTBOUND_DISCOVERY_TABLE_DEF,
    AGENT_TEMPLATE_TABLE_DEF,
    EMBEDDING_TEMPLATE_TABLE_DEF,
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
    PERMISSIONS_TEMPLATE_TABLE_DEF,
    MCP_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_CONTAINER_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    SKILL_PREBUILT_TEMPLATE_TABLE_DEF,
)

ALL_TABLE_DEFINITIONS = (
    INSTANCE_INFO_TABLE_DEF,
    MANAGER_IDENTITY_TABLE_DEF,
    INSTANCE_ENC_PUBKEY_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    _MEMORY_CONFIG_TABLE_DEF,
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
)


async def _ensure_a2a_private_network_column(handler: DBHandler) -> None:
    if not isinstance(handler, SQLAlchemyHandler):
        return

    async with handler.engine.begin() as connection:

        def migrate(sync_connection) -> None:
            table_name = A2A_DISCOVERY_SETTINGS_TABLE_DEF.table_name
            columns = {item["name"] for item in inspect(sync_connection).get_columns(table_name)}
            if "allow_private_network" in columns:
                return
            quote = sync_connection.dialect.identifier_preparer.quote
            sync_connection.execute(
                text(
                    f"ALTER TABLE {quote(table_name)} ADD COLUMN "
                    f"{quote('allow_private_network')} BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )

        await connection.run_sync(migrate)


async def init_all_tables(handler: DBHandler) -> None:
    for table_def in ALL_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
    await _ensure_a2a_private_network_column(handler)
