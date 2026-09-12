# coding: utf-8
"""Runtime 本地证书绑定状态的持久化模型，与 Identity Center 无关。

本模块仅映射 Runtime 的公开状态，不映射部署专用敏感列。
完整材料由部署工具保存在 Gateway 库，Runtime 库不保存各角色私钥。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)
from openjiuwen_runtime.foundation.security.link_material_store import (
    assert_binding_state_compatible,
    assert_managed_state,
)

from .link_mtls import LinkMTLSConfig, LinkMTLSError, LinkMTLSMode
from .util import utc_now

LINK_BINDING_STATE_TABLE = "link_binding_state"
LOCAL_SERVICE_ROLE = "runtime"
PROTOCOL_VERSION = "0.0.1"

LINK_BINDING_STATE_TABLE_DEF = TableDefinition(
    table_name=LINK_BINDING_STATE_TABLE,
    columns=[
        ColumnDefinition(
            "service_role", "string", length=32, primary_key=True, nullable=False
        ),
        ColumnDefinition("mtls_deployment_id", "string", length=64, nullable=False),
        ColumnDefinition("mtls_binding_id", "string", length=64, nullable=False),
        ColumnDefinition("protocol_version", "string", length=32, nullable=False),
        ColumnDefinition("mtls_binding_epoch", "integer", nullable=False),
        ColumnDefinition("local_cert_pem", "text", nullable=False),
        ColumnDefinition(
            "local_cert_fingerprint", "string", length=128, nullable=False
        ),
        ColumnDefinition("private_key_ref", "string", length=512, nullable=False),
        ColumnDefinition("peer_trust_bundle_pem", "text", nullable=False),
        ColumnDefinition(
            "agentserver_secret_ref", "string", length=253, nullable=False
        ),
        ColumnDefinition(
            "status", "string", length=32, nullable=False, default="active"
        ),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(
            ["mtls_deployment_id"],
            unique=True,
            name="uq_link_binding_state_mtls_deployment_id",
        ),
        IndexDefinition(
            ["mtls_binding_id"],
            unique=True,
            name="uq_link_binding_state_mtls_binding_id",
        ),
        IndexDefinition(["status"], unique=False, name="ix_link_binding_state_status"),
    ],
)


async def sync_local_link_binding_state(db: Any, config: LinkMTLSConfig) -> Any | None:
    """把当前生效的公开身份幂等写入 DB；``off`` 不产生任何记录。"""
    if config.mode is LinkMTLSMode.OFF:
        return None
    if not config.identity or not config.cert_file or not config.ca_file:
        # observe 允许只做不完整预检，不制造半条身份记录。
        return None

    now = utc_now()
    payload = {
        "service_role": "runtime",
        "mtls_deployment_id": config.identity.mtls_deployment_id,
        "mtls_binding_id": config.identity.mtls_binding_id,
        "protocol_version": PROTOCOL_VERSION,
        "mtls_binding_epoch": config.identity.mtls_binding_epoch,
        "local_cert_pem": Path(config.cert_file).read_text(encoding="utf-8"),
        "local_cert_fingerprint": config.cert_fingerprint(),
        "private_key_ref": config.key_file or "",
        "peer_trust_bundle_pem": Path(config.ca_file).read_text(encoding="utf-8"),
        "agentserver_secret_ref": config.agentserver_secret,
        "status": "active",
        "updated_at": now,
    }
    row = await db.get(LINK_BINDING_STATE_TABLE, {"service_role": LOCAL_SERVICE_ROLE})
    managed = await assert_managed_state(
        db,
        LOCAL_SERVICE_ROLE,
        config.identity,
        payload["local_cert_fingerprint"],
        required=bool(
            config.profile and config.profile.current().get("persistence") == "database"
        ),
    )
    if managed:
        return (
            row  # Deployment owns the authorized state; service startup is read-only.
        )
    assert_binding_state_compatible(
        row, config.identity, payload["local_cert_fingerprint"]
    )
    if row is None:
        return await db.create(
            LINK_BINDING_STATE_TABLE,
            {"created_at": now, **payload},
        )
    previous_epoch = int(getattr(row, "mtls_binding_epoch", 0) or 0)
    filters = {"service_role": LOCAL_SERVICE_ROLE}
    if hasattr(row, "mtls_binding_epoch"):
        filters["mtls_binding_epoch"] = previous_epoch
        filters["status"] = getattr(row, "status", "active")
    updated = await db.update(LINK_BINDING_STATE_TABLE, filters, payload)
    # DBHandler.update re-reads with the OLD filters, so an epoch change may
    # return None even after a successful conditional update.
    if hasattr(row, "mtls_binding_epoch"):
        updated = await db.get(
            LINK_BINDING_STATE_TABLE, {"service_role": LOCAL_SERVICE_ROLE}
        )
        checked_fields = (
            "mtls_binding_id",
            "mtls_binding_epoch",
            "status",
            "local_cert_fingerprint",
        )
        if updated is None or any(
            getattr(updated, key, None) != payload.get(key) for key in checked_fields
        ):
            raise LinkMTLSError(
                "link binding state changed concurrently; restart with the current profile"
            )
    return updated
