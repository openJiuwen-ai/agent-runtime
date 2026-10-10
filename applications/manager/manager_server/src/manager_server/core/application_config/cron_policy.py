# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""定时任务面板个数上限：Manager 库 + 下发 Gateway。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.manager_config_push import gateway_request

_CRON_POLICY_TABLE = "cron_policy"
_GATEWAY_PATH = "/api/v1/cron-policy"


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _require_limit(raw: Any) -> int:
    if raw is None or isinstance(raw, bool):
        raise ValueError("max_jobs_per_user must be an integer >= 0")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_jobs_per_user must be an integer >= 0") from exc
    if value < 0:
        raise ValueError("max_jobs_per_user must be an integer >= 0")
    return value


def _row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "max_jobs_per_user": _require_limit(getattr(obj, "max_jobs_per_user", 5)),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


def _row_to_gateway_body(obj: Any) -> dict[str, Any]:
    return {"max_jobs_per_user": _require_limit(getattr(obj, "max_jobs_per_user", 5))}


async def push_cron_policy_sync_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """全量同步：将该实例的 cron_policy PUT 到 Gateway。没有托管行则跳过。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")

    row = await handler.get(_CRON_POLICY_TABLE, {"jiuwenclaw_id": jid})
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


class CronPolicyService:

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self, jiuwenclaw_id: str) -> dict[str, Any] | None:
        existing = await self._handler.get(
            _CRON_POLICY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            return None
        return _row_to_dict(existing)

    async def upsert(self, jiuwenclaw_id: str, *, max_jobs_per_user: int) -> dict[str, Any]:
        from manager_server.infrastructure.utils import utc_now

        limit = _require_limit(max_jobs_per_user)
        now = utc_now()
        gateway_body = {"max_jobs_per_user": limit}
        try:
            await gateway_request(jiuwenclaw_id, "PUT", _GATEWAY_PATH, gateway_body)
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        existing = await self._handler.get(
            _CRON_POLICY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is not None:
            updated = await self._handler.update(
                _CRON_POLICY_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                {"max_jobs_per_user": limit, "updated_at": now},
            )
            if updated is None:
                raise ValueError("failed to update cron policy")
            return _row_to_dict(updated)

        created = await self._handler.create(
            _CRON_POLICY_TABLE,
            {
                "jiuwenclaw_id": jiuwenclaw_id,
                "max_jobs_per_user": limit,
                "created_at": now,
                "updated_at": now,
            },
        )
        if created is None:
            raise ValueError("failed to create cron policy")
        return _row_to_dict(created)

    async def delete(self, jiuwenclaw_id: str) -> None:
        existing = await self._handler.get(
            _CRON_POLICY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("cron policy not found")
        try:
            await gateway_request(jiuwenclaw_id, "DELETE", _GATEWAY_PATH, {})
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc
        deleted = await self._handler.delete(
            _CRON_POLICY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if not deleted:
            raise ValueError("failed to delete cron policy")
