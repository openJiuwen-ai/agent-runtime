"""Agent 模板（agent_definition）表定义。

由有效 Agent 配置模板 × 有效运行时模板派生；``(agent_config_template_id,
runtime_template_id)`` 唯一，``agent_id`` 为 1.0 算法生成的 43 位稳定标识。
模板停用/删除时逻辑失效（``deleted=1``），物理行保留以便恢复时沿用自定义名称。
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
        # PG 标识符上限 63；自动生成的 ix_<table>_<cols...> 会超长
        IndexDefinition(
            ["agent_config_template_id", "runtime_template_id"],
            unique=True,
            name="uq_agent_id_rt_id",
        ),
        IndexDefinition(["runtime_template_id"], unique=False),
        IndexDefinition(["agent_name"], unique=False),
    ],
)
