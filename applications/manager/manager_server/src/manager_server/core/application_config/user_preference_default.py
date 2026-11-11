# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""集群默认语言：Manager 库镜像 + 下发 Gateway ``user_preference.__default__``。

无 DELETE：设计 C14，Manager 不提供删除默认行；生命周期仅在有镜像时 push。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.manager_config_push import gateway_request

_TABLE = "user_preference_default"
_GATEWAY_PATH = "/api/v1/user-preference-default"
_ALLOWED = frozenset({"zh", "en"})
PreferredLanguage = Literal["zh", "en"]
_UNSET = object()


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _require_language(raw: Any) -> PreferredLanguage:
    if not isinstance(raw, str):
        raise ValueError("preferred_language must be zh or en")
    lang = raw.strip().lower()
    if lang not in _ALLOWED:
        raise ValueError("preferred_language must be zh or en")
    return lang  # type: ignore[return-value]


def _normalize_extras(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("extras must be a JSON object or null")
    if "preferred_language" in raw:
        raise ValueError("preferred_language must not be stored in extras")
    return dict(raw)


def _row_extras(obj: Any) -> dict[str, Any] | None:
    raw = getattr(obj, "extras", None)
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    return None


def _row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "preferred_language": _require_language(
            getattr(obj, "preferred_language", None)
        ),
        "extras": _row_extras(obj),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


def _row_to_gateway_body(obj: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "preferred_language": _require_language(
            getattr(obj, "preferred_language", None)
        )
    }
    extras = _row_extras(obj)
    if extras is not None:
        body["extras"] = extras
    return body


async def push_user_preference_default_sync_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """全量同步：有镜像才 PUT Gateway；无镜像跳过（不自动建 ``__default__``）。"""
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
        _row_to_gateway_body(row),
    )


class UserPreferenceDefaultService:

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self, jiuwenclaw_id: str) -> dict[str, Any] | None:
        existing = await self._handler.get(
            _TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            return None
        return _row_to_dict(existing)

    async def upsert(
        self,
        jiuwenclaw_id: str,
        *,
        preferred_language: str,
        extras: Any = _UNSET,
    ) -> dict[str, Any]:
        from manager_server.infrastructure.utils import utc_now

        lang = _require_language(preferred_language)
        extras_provided = extras is not _UNSET
        extras_value = _normalize_extras(extras) if extras_provided else None
        now = utc_now()
        gateway_body: dict[str, Any] = {"preferred_language": lang}
        if extras_provided:
            gateway_body["extras"] = extras_value
        try:
            await gateway_request(jiuwenclaw_id, "PUT", _GATEWAY_PATH, gateway_body)
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        existing = await self._handler.get(
            _TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is not None:
            updates: dict[str, Any] = {
                "preferred_language": lang,
                "updated_at": now,
            }
            if extras_provided:
                updates["extras"] = extras_value
            updated = await self._handler.update(
                _TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                updates,
            )
            if updated is None:
                raise ValueError("failed to update user preference default")
            return _row_to_dict(updated)

        created = await self._handler.create(
            _TABLE,
            {
                "jiuwenclaw_id": jiuwenclaw_id,
                "preferred_language": lang,
                "extras": extras_value if extras_provided else None,
                "created_at": now,
                "updated_at": now,
            },
        )
        if created is None:
            raise ValueError("failed to create user preference default")
        return _row_to_dict(created)
