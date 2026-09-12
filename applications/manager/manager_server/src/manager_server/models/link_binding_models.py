"""实例链路绑定表。

``*_active_key`` 仅在绑定生效时有值，并带唯一索引：数据库层保证同一 Gateway
或 Runtime 不能同时绑定到两个 JiuwenClaw 实例；解绑置空后可安全重新绑定。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

INSTANCE_LINK_BINDING_TABLE_DEF = TableDefinition(
    table_name="instance_link_binding",
    columns=[
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("mtls_binding_id", "string", length=64, nullable=False),
        ColumnDefinition("mtls_binding_epoch", "integer", nullable=False),
        ColumnDefinition("protocol_version", "string", length=32, nullable=False),
        ColumnDefinition("mtls_gateway_endpoint", "string", length=128, nullable=False),
        ColumnDefinition("mtls_runtime_endpoint", "string", length=128, nullable=False),
        ColumnDefinition("mtls_gateway_active_key", "string", length=128, nullable=True),
        ColumnDefinition("mtls_runtime_active_key", "string", length=128, nullable=True),
        ColumnDefinition("gateway_cert_fingerprint", "string", length=128, nullable=False),
        ColumnDefinition("runtime_cert_fingerprint", "string", length=128, nullable=False),
        ColumnDefinition("agentserver_cert_fingerprint", "string", length=128, nullable=False),
        ColumnDefinition("trust_bundle_ref", "string", length=512, nullable=False),
        ColumnDefinition("status", "string", length=32, nullable=False, default="bound"),
        ColumnDefinition("bound_at", "datetime", nullable=False),
        ColumnDefinition("unbound_at", "datetime", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
        ColumnDefinition("updated_by", "string", length=64, nullable=False),
        ColumnDefinition("data", "json", nullable=True),
    ],
    indexes=[
        IndexDefinition(["mtls_binding_id"], unique=True, name="uq_link_binding_mtls_binding_id"),
        IndexDefinition(
            ["mtls_gateway_active_key"], unique=True, name="uq_link_binding_gateway_active"
        ),
        IndexDefinition(
            ["mtls_runtime_active_key"], unique=True, name="uq_link_binding_runtime_active"
        ),
        IndexDefinition(["status"], unique=False, name="ix_link_binding_status"),
    ],
)
