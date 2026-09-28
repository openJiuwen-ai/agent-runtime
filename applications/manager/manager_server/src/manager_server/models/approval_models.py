"""工作区扩容审批单与不可变操作记录。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

APPROVAL_ORDER_TABLE_DEF = TableDefinition(
    table_name="approval_order",
    columns=[
        ColumnDefinition("id", "bigint", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("cluster_id", "string", length=64, nullable=False),
        ColumnDefinition("order_num", "string", length=64, nullable=False),
        ColumnDefinition("title", "string", length=256, nullable=False),
        ColumnDefinition("applicant_id", "string", length=64, nullable=False),
        ColumnDefinition("approver_id", "string", length=64, nullable=True),
        ColumnDefinition("group_id", "string", length=64, nullable=True),
        ColumnDefinition("bot_id", "string", length=64, nullable=False),
        ColumnDefinition("status", "string", length=16, nullable=False),
        ColumnDefinition("finished_at", "datetime3", nullable=True),
        ColumnDefinition("business_type", "string", length=64, nullable=False),
        ColumnDefinition("reason", "string", length=1024, nullable=False),
        ColumnDefinition("apply_data", "json", nullable=False),
        ColumnDefinition("result_data", "json", nullable=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime3", nullable=False),
        ColumnDefinition("created_by", "string", length=64, nullable=True),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
    indexes=[
        IndexDefinition(["order_num"], unique=True),
    ],
)


APPROVAL_RECORD_TABLE_DEF = TableDefinition(
    table_name="approval_record",
    columns=[
        ColumnDefinition("id", "bigint", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("record_id", "string", length=64, nullable=False),
        ColumnDefinition("order_num", "string", length=64, nullable=False),
        ColumnDefinition("operator_id", "string", length=64, nullable=False),
        ColumnDefinition("action", "string", length=16, nullable=False),
        ColumnDefinition("comment", "string", length=1024, nullable=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime3", nullable=False),
        ColumnDefinition("created_by", "string", length=64, nullable=True),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
    indexes=[
        IndexDefinition(["record_id"], unique=True),
    ],
)


APPROVAL_TABLE_DEFINITIONS = (
    APPROVAL_ORDER_TABLE_DEF,
    APPROVAL_RECORD_TABLE_DEF,
)
