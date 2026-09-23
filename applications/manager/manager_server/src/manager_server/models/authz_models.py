"""Manager 产品授权表：权限、角色、角色权限与用户指派。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

AUTHZ_PERMISSION_TABLE_DEF = TableDefinition(
    table_name="authz_permission",
    columns=[
        ColumnDefinition("id", "bigint", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("permission_id", "string", length=128, nullable=False),
        ColumnDefinition("name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("resource_type", "string", length=32, nullable=False),
        ColumnDefinition("action", "string", length=32, nullable=False),
        ColumnDefinition("scope", "string", length=16, nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime3", nullable=False),
        ColumnDefinition("created_by", "string", length=64, nullable=True),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
    indexes=[IndexDefinition(["permission_id"], unique=True)],
)


AUTHZ_ROLE_TABLE_DEF = TableDefinition(
    table_name="authz_role",
    columns=[
        ColumnDefinition("id", "bigint", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("role_id", "string", length=64, nullable=False),
        ColumnDefinition("name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("scope", "string", length=16, nullable=False),
        ColumnDefinition("is_system", "boolean", nullable=False, default=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime3", nullable=False),
        ColumnDefinition("created_by", "string", length=64, nullable=True),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
    indexes=[IndexDefinition(["role_id"], unique=True)],
)


AUTHZ_ROLE_PERMISSION_TABLE_DEF = TableDefinition(
    table_name="authz_role_permission",
    columns=[
        ColumnDefinition("id", "bigint", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("role_id", "string", length=64, nullable=False),
        ColumnDefinition("permission_id", "string", length=64, nullable=False),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime3", nullable=False),
        ColumnDefinition("created_by", "string", length=64, nullable=True),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
    indexes=[IndexDefinition(["role_id", "permission_id"], unique=True)],
)


AUTHZ_ROLE_USER_TABLE_DEF = TableDefinition(
    table_name="authz_role_user",
    columns=[
        ColumnDefinition("id", "bigint", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("role_id", "string", length=64, nullable=False),
        ColumnDefinition("user_id", "string", length=64, nullable=False),
        ColumnDefinition("granted_by", "string", length=64, nullable=True),
        ColumnDefinition("expires_at", "datetime3", nullable=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime3", nullable=False),
        ColumnDefinition("created_by", "string", length=64, nullable=True),
        ColumnDefinition("updated_at", "datetime3", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=True),
    ],
    indexes=[IndexDefinition(["role_id", "user_id"], unique=True)],
)


AUTHZ_TABLE_DEFINITIONS = (
    AUTHZ_PERMISSION_TABLE_DEF,
    AUTHZ_ROLE_TABLE_DEF,
    AUTHZ_ROLE_PERMISSION_TABLE_DEF,
    AUTHZ_ROLE_USER_TABLE_DEF,
)
