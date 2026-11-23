# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""内容脱敏配置业务逻辑：Manager 落库 + 下发 Gateway 热更新。

与 jiuwenswarm Gateway 的 ``PUT/DELETE /api/v1/content-masking`` 对齐：
``{"enabled": bool, "rules": [{"title": str, "pattern": str}]}``，整段覆盖。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.manager_config_push import gateway_request

_CONTENT_MASKING_TABLE = "content_masking_config"

_MAX_RULES = 32
_MAX_TITLE = 64
_MAX_PATTERN = 256


def _format_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _normalize_rules(rules: Any) -> list[dict[str, str]]:
    if rules is None:
        return []
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except json.JSONDecodeError as exc:
            raise ValueError("rules must be a JSON array") from exc
    if not isinstance(rules, list):
        raise ValueError("rules must be an array")
    if len(rules) > _MAX_RULES:
        raise ValueError(f"at most {_MAX_RULES} rules are allowed")

    cleaned: list[dict[str, str]] = []
    for index, item in enumerate(rules):
        if not isinstance(item, dict):
            raise ValueError(f"rule[{index}] must be an object")
        title = str(item.get("title", "")).strip()
        pattern = str(item.get("pattern", "")).strip()
        if not title:
            raise ValueError(f"rule[{index}].title is required")
        if len(title) > _MAX_TITLE:
            raise ValueError(f"rule[{index}].title exceeds {_MAX_TITLE} characters")
        if not pattern:
            raise ValueError(f"rule[{index}].pattern is required")
        if len(pattern) > _MAX_PATTERN:
            raise ValueError(f"rule[{index}].pattern exceeds {_MAX_PATTERN} characters")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"rule[{index}].pattern is invalid: {exc}") from exc
        cleaned.append({"title": title, "pattern": pattern})
    return cleaned


def _row_to_dict(obj: Any) -> dict[str, Any]:
    rules = getattr(obj, "rules", None)
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except json.JSONDecodeError:
            rules = []
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "enabled": bool(getattr(obj, "enabled", False)),
        "rules": rules if isinstance(rules, list) else [],
        "source": getattr(obj, "source", "manager"),
        "revision": getattr(obj, "revision", 1),
        "created_at": _format_ts(getattr(obj, "created_at", None)),
        "updated_at": _format_ts(getattr(obj, "updated_at", None)),
    }


async def push_content_masking_config_sync_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """全量同步：将 Manager 库中该实例的内容脱敏配置 PUT 到 Gateway。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")

    row = await handler.get(_CONTENT_MASKING_TABLE, {"jiuwenclaw_id": jid})
    if row is None:
        return {
            "success_flag": True,
            "result": {"synced": False},
            "transport": "http",
        }
    data = _row_to_dict(row)
    return await gateway_request(
        jid,
        "PUT",
        "/api/v1/content-masking",
        {"enabled": data["enabled"], "rules": data["rules"]},
    )


class ContentMaskingConfigService:
    """网页出站内容脱敏配置：单文档，PUT 全量替换、DELETE 回退 config.yaml。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def get(self, jiuwenclaw_id: str) -> dict[str, Any] | None:
        existing = await self._handler.get(
            _CONTENT_MASKING_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            return None
        return _row_to_dict(existing)

    async def upsert(
        self,
        jiuwenclaw_id: str,
        *,
        enabled: bool,
        rules: Any,
    ) -> dict[str, Any]:
        from manager_server.infrastructure.utils import utc_now

        cleaned = _normalize_rules(rules)
        gateway_body = {"enabled": bool(enabled), "rules": cleaned}

        # 先下发 Gateway，成功后再写 Manager 镜像库（与 logging/audit-log 一致）
        try:
            await gateway_request(
                jiuwenclaw_id,
                "PUT",
                "/api/v1/content-masking",
                gateway_body,
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        now = utc_now()
        existing = await self._handler.get(
            _CONTENT_MASKING_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is not None:
            updated = await self._handler.update(
                _CONTENT_MASKING_TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                {
                    "enabled": bool(enabled),
                    "rules": cleaned,
                    "source": "manager",
                    "revision": int(getattr(existing, "revision", 1) or 1) + 1,
                    "updated_at": now,
                },
            )
            if updated is None:
                raise ValueError("failed to update content masking config")
            return _row_to_dict(updated)

        created = await self._handler.create(
            _CONTENT_MASKING_TABLE,
            {
                "jiuwenclaw_id": jiuwenclaw_id,
                "enabled": bool(enabled),
                "rules": cleaned,
                "source": "manager",
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        if created is None:
            raise ValueError("failed to create content masking config")
        return _row_to_dict(created)

    async def delete(self, jiuwenclaw_id: str) -> None:
        existing = await self._handler.get(
            _CONTENT_MASKING_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if existing is None:
            raise ValueError("content masking config not found")

        try:
            await gateway_request(
                jiuwenclaw_id,
                "DELETE",
                "/api/v1/content-masking",
                {},
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

        deleted = await self._handler.delete(
            _CONTENT_MASKING_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
        )
        if not deleted:
            raise ValueError("failed to delete content masking config")
