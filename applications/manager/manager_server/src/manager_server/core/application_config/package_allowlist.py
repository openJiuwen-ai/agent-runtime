# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""实例专家包允许名单：Manager 库 + 下发 Gateway。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.manager_config_push import gateway_request

logger = logging.getLogger(__name__)

_TABLE = "package_allowlist"
_GATEWAY_PATH = "/api/v1/package-allowlists"
_CATALOG_PATH = "/api/v1/package-allowlists/catalog"
_KINDS = {"agent_template": "agent_template", "agent_group": "agent_group"}


def _package_id_is_invalid(package_id: str) -> bool:
    if not package_id or package_id in {".", ".."} or len(package_id) > 128:
        return True
    return "/" in package_id or "\\" in package_id


def normalize_packages(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("packages must be a list")
    seen: set[tuple[str, str]] = set()
    packages: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("package grant must be an object")
        kind = _KINDS.get(str(item.get("kind") or "").strip())
        if kind is None:
            raise ValueError("kind must be agent_template or agent_group")
        package_id = str(item.get("id") or "").strip()
        if _package_id_is_invalid(package_id):
            raise ValueError("package id is invalid")
        key = (kind, package_id)
        if key in seen:
            continue
        seen.add(key)
        record: dict[str, Any] = {"kind": kind, "id": package_id}
        match_expr = _normalize_match_expr(item.get("match_expr"))
        if match_expr not in (None, [], ""):
            record["match_expr"] = match_expr
        packages.append(record)
    return packages


def _normalize_match_expr(raw: Any) -> Any:
    from manager_server.infrastructure.match_expr import validate_agent_resource_match_expr

    return validate_agent_resource_match_expr(raw)


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _packages_from_row(obj: Any) -> list[dict[str, Any]]:
    return normalize_packages(getattr(obj, "packages", None))


def _row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "packages": _packages_from_row(obj),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


async def fetch_hub_catalog(jiuwenclaw_id: str) -> dict[str, Any]:
    """向这一台集群要 Hub 目录。调用方不能指定 Hub 地址。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")
    try:
        result = await gateway_request(jid, "GET", _CATALOG_PATH, {})
    except Exception:  # noqa: BLE001
        logger.warning(
            "[package_allowlist] catalog request failed jiuwenclaw_id=%s",
            jid,
            exc_info=True,
        )
        return {"items": [], "error": "catalog unavailable"}
    payload = result.get("result") if isinstance(result, dict) else None
    if not isinstance(payload, dict):
        return {"items": [], "error": "catalog response is empty"}
    raw_items = payload.get("items") or []
    if not isinstance(raw_items, list):
        return {"items": [], "error": "catalog items must be a list"}
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        kind = _KINDS.get(str(item.get("kind") or "").strip())
        package_id = str(item.get("id") or "").strip()
        if kind is None or _package_id_is_invalid(package_id):
            continue
        items.append({
            "kind": kind,
            "id": package_id,
            "display_name": str(item.get("display_name") or package_id).strip() or package_id,
            "version": str(item.get("version") or "").strip(),
        })
    return {"items": items, "error": str(payload.get("error") or "").strip()}


async def push_package_allowlist_sync_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """全量同步：将该实例的允许名单 PUT 到 Gateway。没有托管行则跳过。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")
    row = await handler.get(_TABLE, {"jiuwenclaw_id": jid})
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
        {"packages": _packages_from_row(row)},
    )


class PackageAllowlistService:

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self, jiuwenclaw_id: str) -> dict[str, Any] | None:
        existing = await self._handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if existing is None:
            return None
        return _row_to_dict(existing)

    async def upsert(self, jiuwenclaw_id: str, *, packages: Any) -> dict[str, Any]:
        from manager_server.infrastructure.utils import utc_now

        normalized = normalize_packages(packages)
        now = utc_now()
        try:
            await gateway_request(
                jiuwenclaw_id,
                "PUT",
                _GATEWAY_PATH,
                {"packages": normalized},
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        existing = await self._handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if existing is not None:
            updated = await self._handler.update(
                _TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                {"packages": normalized, "updated_at": now},
            )
            if updated is None:
                raise ValueError("failed to update package allowlist")
            return _row_to_dict(updated)

        created = await self._handler.create(
            _TABLE,
            {
                "jiuwenclaw_id": jiuwenclaw_id,
                "packages": normalized,
                "created_at": now,
                "updated_at": now,
            },
        )
        if created is None:
            raise ValueError("failed to create package allowlist")
        return _row_to_dict(created)

    async def delete(self, jiuwenclaw_id: str) -> None:
        existing = await self._handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if existing is None:
            raise ValueError("package allowlist not found")
        try:
            await gateway_request(jiuwenclaw_id, "DELETE", _GATEWAY_PATH, {})
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc
        deleted = await self._handler.delete(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if not deleted:
            raise ValueError("failed to delete package allowlist")
