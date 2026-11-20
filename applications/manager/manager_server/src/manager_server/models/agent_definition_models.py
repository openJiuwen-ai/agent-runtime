"""Agent 模板（agent_definition）表定义。

由管理员手动维护：``(agent_config_template_id, runtime_template_id)`` 唯一，
``agent_id`` 仍按两者的组合稳定生成；模板名称列不再写入，列表按模板 id 实时查询。
``deleted`` / ``deleted_reason`` 与快照列保留以兼容历史数据，当前逻辑不再使用。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

AGENT_DEFINITION_TABLE_DEF = TableDefinition(
    table_name="agent_definition",
    columns=[
        ColumnDefinition(
            "id", "integer", primary_key=True, autoincrement=True, nullable=False
        ),
        ColumnDefinition("agent_id", "string", length=43, nullable=False),
        ColumnDefinition("agent_name", "string", length=128, nullable=False),
        ColumnDefinition(
            "agent_config_template_id", "string", length=100, nullable=False
        ),
        ColumnDefinition("runtime_template_id", "string", length=100, nullable=False),
        # 快照列：仅用于列表展示与排障，同步时按模板最新名称刷新。
        ColumnDefinition(
            "agent_config_template_name", "string", length=128, nullable=True
        ),
        ColumnDefinition("runtime_template_name", "string", length=128, nullable=True),
        ColumnDefinition("deleted", "boolean", nullable=False, default=False),
        ColumnDefinition("deleted_reason", "string", length=32, nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["agent_id"], unique=True),
        IndexDefinition(
            ["agent_config_template_id", "runtime_template_id"], unique=True
        ),
        IndexDefinition(["runtime_template_id"], unique=False),
        IndexDefinition(["agent_name"], unique=False),
    ],
)
