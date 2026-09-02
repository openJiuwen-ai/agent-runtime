"""Gateway / Runtime 模板引用索引表 ``jid_template_ref``（MDB）。

Gateway 普通模板用 ``template_ref`` 槽位（如 default_model）；
Runtime 服务配置用 ``slot=service_config``，与前者同表共存。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

JID_TEMPLATE_REF_TABLE_DEF = TableDefinition(
    table_name="jid_template_ref",
    columns=[
        ColumnDefinition(
            "jiuwenclaw_id",
            "string",
            length=64,
            primary_key=True,
            nullable=False,
        ),
        ColumnDefinition(
            "slot",
            "string",
            length=128,
            primary_key=True,
            nullable=False,
        ),
        ColumnDefinition(
            "template_id",
            "string",
            length=100,
            primary_key=True,
            nullable=False,
        ),
        ColumnDefinition("ref_count", "integer", nullable=False, default=0),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=False),
    ],
)
