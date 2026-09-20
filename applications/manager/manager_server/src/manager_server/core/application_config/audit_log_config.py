# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""audit-log 配置：MDB 落盘 + Gateway 推送（对标 logging/memory：先 Gateway 再写库）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from openjiuwen_runtime.foundation.audit import (
    AuditLogConfig,
    SchemaValidationError,
)
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.manager_config_push import gateway_request

_AUDIT_LOG_CONFIG_TABLE = "audit_log_config"
_GATEWAY_PATH = "/api/v1/audit-log"


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _strip_service(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dict(payload).items() if k != "service"}


def _node_for_body(cleaned: Mapping[str, Any]) -> str | None:
    if "node" not in cleaned:
        return None
    raw = cleaned.get("node")
    if raw is None:
        return None
    text = str(raw).strip()
    return text if text else None


def _precheck_and_build_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    """预检（foundation AuditLogConfig）并生成入库权威 body（不含 service）。"""
    cleaned = _strip_service(payload)
    try:
        cfg = AuditLogConfig.from_dict(cleaned, validate=True)
    except SchemaValidationError as exc:
        raise ValueError(str(exc)) from exc

    fmt = cfg.format
    otel = cfg.otel
    ntp = cfg.ntp
    return {
        "format": {
            "schema_version": fmt.schema_version,
            "header_fields": list(fmt.header_fields),
            "content_fields": list(fmt.content_fields),
            "required_fields": list(fmt.required_fields),
            "placeholder": fmt.placeholder,
            "timestamp_format": fmt.timestamp_format,
        },
        "ntp": {
            "servers": list(ntp.servers),
            "sync_interval": ntp.sync_interval,
            "max_offset_ms": ntp.max_offset_ms,
            "failover": ntp.failover,
        },
        "otel": {
            "enabled": bool(otel.enabled),
            "endpoint": otel.endpoint,
            "protocol": otel.protocol,
            "headers": dict(otel.headers),
        },
        "data_center": cfg.identity.data_center,
        "system_code": cfg.identity.system_code,
        "node": _node_for_body(cleaned),
    }


def _project_flat_columns(body: Mapping[str, Any]) -> dict[str, Any]:
    fmt = body.get("format") if isinstance(body.get("format"), Mapping) else {}
    otel = body.get("otel") if isinstance(body.get("otel"), Mapping) else {}
    ntp = body.get("ntp") if isinstance(body.get("ntp"), Mapping) else {}
    servers = ntp.get("servers") if isinstance(ntp, Mapping) else []
    if not isinstance(servers, list):
        servers = list(servers) if servers else []
    return {
        "schema_version": str(fmt.get("schema_version") or "1.0.0"),
        "otel_enabled": bool(otel.get("enabled", True)),
        "otel_endpoint": otel.get("endpoint"),
        "otel_protocol": str(otel.get("protocol") or "grpc"),
        "data_center": str(body.get("data_center") or "N"),
        "system_code": str(body.get("system_code") or "-"),
        "node": body.get("node"),
        "ntp_servers": servers,
        "ntp_sync_interval": str(ntp.get("sync_interval") or "300s"),
        "ntp_max_offset_ms": int(ntp.get("max_offset_ms") or 500),
        "ntp_failover": bool(ntp.get("failover", True)),
    }


def _row_to_dict(obj: Any) -> dict[str, Any]:
    body = getattr(obj, "body", None)
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "schema_version": getattr(obj, "schema_version", "1.0.0"),
        "otel_enabled": bool(getattr(obj, "otel_enabled", True)),
        "otel_endpoint": getattr(obj, "otel_endpoint", None),
        "otel_protocol": getattr(obj, "otel_protocol", "grpc"),
        "data_center": getattr(obj, "data_center", "N"),
        "system_code": getattr(obj, "system_code", "-"),
        "node": getattr(obj, "node", None),
        "ntp_servers": getattr(obj, "ntp_servers", []) or [],
        "ntp_sync_interval": getattr(obj, "ntp_sync_interval", "300s"),
        "ntp_max_offset_ms": getattr(obj, "ntp_max_offset_ms", 500),
        "ntp_failover": bool(getattr(obj, "ntp_failover", True)),
        "body": dict(body) if isinstance(body, dict) else body,
        "source": getattr(obj, "source", "manager"),
        "revision": int(getattr(obj, "revision", 1) or 1),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


def _row_to_gateway_body(obj: Any) -> dict[str, Any]:
    body = getattr(obj, "body", None)
    return {
        "schema_version": getattr(obj, "schema_version", "1.0.0"),
        "otel_enabled": bool(getattr(obj, "otel_enabled", True)),
        "otel_endpoint": getattr(obj, "otel_endpoint", None),
        "otel_protocol": getattr(obj, "otel_protocol", "grpc"),
        "data_center": getattr(obj, "data_center", "N"),
        "system_code": getattr(obj, "system_code", "-"),
        "node": getattr(obj, "node", None),
        "ntp_servers": getattr(obj, "ntp_servers", []) or [],
        "ntp_sync_interval": getattr(obj, "ntp_sync_interval", "300s"),
        "ntp_max_offset_ms": getattr(obj, "ntp_max_offset_ms", 500),
        "ntp_failover": bool(getattr(obj, "ntp_failover", True)),
        "body": dict(body) if isinstance(body, dict) else (body or {}),
        "source": getattr(obj, "source", "manager"),
        "revision": int(getattr(obj, "revision", 1) or 1),
    }


def _gateway_body_from_parts(
    *,
    flat: Mapping[str, Any],
    body: Mapping[str, Any],
    source: str,
    revision: int,
) -> dict[str, Any]:
    return {
        **dict(flat),
        "body": dict(body),
        "source": source,
        "revision": revision,
    }


async def push_audit_log_config_sync_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """全量同步：将 Manager MDB 中该实例 audit-log 配置 PUT 到 Gateway。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")

    row = await handler.get(_AUDIT_LOG_CONFIG_TABLE, {"jiuwenclaw_id": jid})
    if row is None:
        return {
            "success_flag": True,
            "result": {"synced": False},
            "transport": "http",
        }
    return await gateway_request(
        jid,
        "PUT",
        _GATEWAY_PATH,
        _row_to_gateway_body(row),
    )


class AuditLogConfigService:
    """Audit-log 配置服务：预检 → 先推 Gateway → 再写 MDB。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self, jiuwenclaw_id: str) -> dict[str, Any] | None:
        existing = await self._handler.get(
            _AUDIT_LOG_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            return None
        return _row_to_dict(existing)

    async def upsert(
        self,
        jiuwenclaw_id: str,
        payload: Mapping[str, Any],
        *,
        source: str = "manager",
    ) -> dict[str, Any]:
        from manager_server.infrastructure.utils import utc_now

        body = _precheck_and_build_body(payload)
        flat = _project_flat_columns(body)
        now = utc_now()
        existing = await self._handler.get(
            _AUDIT_LOG_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )

        if existing is not None:
            revision = int(getattr(existing, "revision", 1) or 1) + 1
        else:
            revision = 1

        gateway_body = _gateway_body_from_parts(
            flat=flat,
            body=body,
            source=source,
            revision=revision,
        )
        try:
            await gateway_request(
                jiuwenclaw_id,
                "PUT",
                _GATEWAY_PATH,
                gateway_body,
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        if existing is not None:
            update_data: dict[str, Any] = {
                **flat,
                "body": body,
                "source": source,
                "revision": revision,
                "updated_at": now,
            }
            updated = await self._handler.update(
                _AUDIT_LOG_CONFIG_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            if updated is None:
                raise ValueError("failed to update audit log config")
            return _row_to_dict(updated)

        row_data = {
            "jiuwenclaw_id": jiuwenclaw_id,
            **flat,
            "body": body,
            "source": source,
            "revision": revision,
            "created_at": now,
            "updated_at": now,
        }
        created = await self._handler.create(_AUDIT_LOG_CONFIG_TABLE, row_data)
        if created is None:
            raise ValueError("failed to create audit log config")
        return _row_to_dict(created)

    async def delete(self, jiuwenclaw_id: str) -> None:
        existing = await self._handler.get(
            _AUDIT_LOG_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("audit log config not found")

        try:
            await gateway_request(jiuwenclaw_id, "DELETE", _GATEWAY_PATH, {})
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _AUDIT_LOG_CONFIG_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if not deleted:
            raise ValueError("failed to delete audit log config")
