"""Claw Manager 纳管表：instance_info。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

INSTANCE_INFO_TABLE_DEF = TableDefinition(
    table_name="instance_info",
    columns=[
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("jiuwenclaw_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=4096, nullable=True),
        # 共用命名空间
        ColumnDefinition(
            "namespace", "string", length=64, nullable=False, default="default"
        ),
        # Gateway 侧（config_host → status → last_alive）
        ColumnDefinition("gateway_config_host", "string", length=512, nullable=False),
        ColumnDefinition(
            "gateway_status", "string", length=32, nullable=False, default="pending"
        ),
        ColumnDefinition("gateway_last_alive", "datetime", nullable=True),
        # Runtime 侧
        ColumnDefinition("runtime_config_host", "string", length=512, nullable=False),
        ColumnDefinition(
            "runtime_status", "string", length=32, nullable=False, default="pending"
        ),
        ColumnDefinition("runtime_last_alive", "datetime", nullable=True),
        ColumnDefinition("space_id", "string", length=64, nullable=False, default="default"),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("created_by", "string", length=64, nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
    indexes=[
        IndexDefinition(["gateway_status"], unique=False),
        IndexDefinition(["updated_at"], unique=False),
    ],
)
