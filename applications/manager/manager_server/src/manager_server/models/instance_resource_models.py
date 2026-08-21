"""实例资源表定义：instance_agent_resource / instance_service_resource。

实例资源 = 把 Agent / 服务资源模板挂到实例上（授权即实例化）；
谁能用由 match_expr 判定。与「实例准入」instance_grant 正交。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

# 实例 Agent 资源（授权即实例化）。替换原 bot_visibility / agent_grant / instance_agent。
# 每次添加生成独立 resource_id；同一模板可多实例；谁可用由 match_expr 判定。
INSTANCE_AGENT_RESOURCE_TABLE_DEF = TableDefinition(
    table_name="instance_agent_resource",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("resource_id", "string", length=100, nullable=False),
        ColumnDefinition("resource_name", "string", length=128, nullable=False),
        ColumnDefinition("resource_desc", "string", length=512, nullable=True),
        ColumnDefinition("ref_template_id", "string", length=100, nullable=False),
        ColumnDefinition("match_expr", "json", nullable=True),
        ColumnDefinition("granted_by", "string", length=64, nullable=True),
        ColumnDefinition("expires_at", "datetime", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["resource_id"], unique=True),
    ],
)

# 实例服务资源（授权即实例化）。每次添加生成独立 resource_id；
# 含 resource_name / resource_desc；谁可用由 match_expr 判定。
INSTANCE_SERVICE_RESOURCE_TABLE_DEF = TableDefinition(
    table_name="instance_service_resource",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("resource_id", "string", length=100, nullable=False),
        ColumnDefinition("resource_name", "string", length=128, nullable=False),
        ColumnDefinition("resource_desc", "string", length=512, nullable=True),
        ColumnDefinition("ref_template_id", "string", length=100, nullable=False),
        ColumnDefinition("match_expr", "json", nullable=True),
        ColumnDefinition("priority", "integer", nullable=False, default=0),
        ColumnDefinition("granted_by", "string", length=64, nullable=True),
        ColumnDefinition("expires_at", "datetime", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["resource_id"], unique=True),
    ],
)

INSTANCE_RESOURCE_TABLE_DEFINITIONS = (
    INSTANCE_AGENT_RESOURCE_TABLE_DEF,
    INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
)
