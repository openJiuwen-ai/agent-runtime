"""Skill 上传授权表定义：skill_upload_permission。

管理员直接授权与审批通过共用同一张授权记录表；审批状态维护在
``approval_order``，本表的 ``enabled`` 独立维护授权是否生效
（设计文档 §10.2：无记录或 ``enabled=false`` 均表示未授权）。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

SKILL_UPLOAD_PERMISSION_TABLE_DEF = TableDefinition(
    table_name="skill_upload_permission",
    columns=[
        ColumnDefinition("id", "bigint", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("cluster_id", "string", length=64, nullable=False),
        ColumnDefinition("subject_type", "string", length=16, nullable=False),
        ColumnDefinition("subject_id", "string", length=64, nullable=False),
        ColumnDefinition("source", "string", length=16, nullable=False, default="admin"),
        ColumnDefinition("source_order_num", "string", length=64, nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("granted_by", "string", length=64, nullable=True),
        ColumnDefinition("expires_at", "datetime3", nullable=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime3", nullable=False),
        ColumnDefinition("created_by", "string", length=64, nullable=True),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
    indexes=[
        IndexDefinition(["cluster_id", "subject_type", "subject_id"], unique=True),
    ],
)

SKILL_UPLOAD_TABLE_DEFINITIONS = (SKILL_UPLOAD_PERMISSION_TABLE_DEF,)
