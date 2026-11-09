"""实例准入表定义：instance_grant。

用户/组织 ↔ 实例准入绑定（按 jiuwenclaw_id）；与「实例资源」instance_resource 正交。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

# 用户/组织 ↔ 实例准入绑定（合并原 user_gateway + org_gateway）。
INSTANCE_GRANT_TABLE_DEF = TableDefinition(
    table_name="instance_grant",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("subject_type", "string", length=16, nullable=False),
        ColumnDefinition("subject_id", "string", length=64, nullable=False),
        ColumnDefinition("granted_by", "string", length=64, nullable=True),
        ColumnDefinition("login_policy", "string", length=16, nullable=False, default="allow"),
        ColumnDefinition("expires_at", "datetime", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        # 一实例内同一主体一行；最左前缀覆盖"某实例花名册"(WHERE jiuwenclaw_id=)。
        IndexDefinition(["jiuwenclaw_id", "subject_type", "subject_id"], unique=True),
    ],
)

INSTANCE_ACCESS_TABLE_DEFINITIONS = (
    INSTANCE_GRANT_TABLE_DEF,
)
