# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""工作区用量：从 Gateway 拉取缓存，写入 Manager 后再查询返回。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.quota.workspace_quota_policy import (
    WorkspaceQuotaPolicyService,
    select_effective_policy,
)
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.utils import iso_datetime, utc_now
from manager_server.manager_config_push import gateway_request
from manager_server.models.quota_models import WORKSPACE_QUOTA_USAGE_TABLE_DEF
from manager_server.schemas.quota_schemas import WorkspaceQuotaUsageListQuery

logger = get_logger(__name__)

_TABLE = WORKSPACE_QUOTA_USAGE_TABLE_DEF.table_name
_GATEWAY_PATH = "/api/v1/workspace-quota/usage"
_PAGE = 500
_GATEWAY_PAGE = 100
_VALID_STATUS = frozenset({"ok", "warn", "block"})
_SUMMARY_TOP = 10


def _g(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _parse_reported_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = _as_str(value).strip()
    if not text:
        return utc_now()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return utc_now()


def _usage_status(*, used_bytes: int, limit_bytes: int, soft_percent: int, hard_percent: int) -> str:
    """按 §2.3：ok / warn / block。limit_bytes=-1 视为无限制。"""
    if limit_bytes < 0:
        return "ok"
    if limit_bytes == 0:
        return "block" if used_bytes > 0 else "ok"
    soft = limit_bytes * max(soft_percent, 0) / 100.0
    hard = limit_bytes * max(hard_percent, 0) / 100.0
    if used_bytes >= hard:
        return "block"
    if used_bytes >= soft:
        return "warn"
    return "ok"


def _usage_dict(row: Any) -> dict[str, Any]:
    return {
        "cluster_id": _as_str(_g(row, "cluster_id")),
        "user_id": _as_str(_g(row, "user_id")),
        "group_id": _as_str(_g(row, "group_id")),
        "bot_id": _as_str(_g(row, "bot_id")),
        "used_bytes": _as_int(_g(row, "used_bytes")),
        "limit_bytes": _as_int(_g(row, "limit_bytes")),
        "source_policy_id": _as_str(_g(row, "source_policy_id")),
        "status": _as_str(_g(row, "status"), "ok"),
        "reported_at": iso_datetime(_g(row, "reported_at")),
    }


def _matches_usage_search(item: dict[str, Any], query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = (
        str(item.get("user_id") or ""),
        str(item.get("group_id") or ""),
        str(item.get("bot_id") or ""),
    )
    return any(needle in field.lower() for field in fields)


def _build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok_count = 0
    warn_count = 0
    block_count = 0
    total_used = 0
    for row in rows:
        total_used += int(row["used_bytes"])
        status = row["status"]
        if status == "warn":
            warn_count += 1
        elif status == "block":
            block_count += 1
        else:
            ok_count += 1
    top = sorted(rows, key=lambda item: int(item["used_bytes"]), reverse=True)[:_SUMMARY_TOP]
    return {
        "subject_count": len(rows),
        "total_used_bytes": total_used,
        "ok_count": ok_count,
        "warn_count": warn_count,
        "block_count": block_count,
        "top_items": [
            {
                "user_id": item["user_id"],
                "group_id": item["group_id"],
                "bot_id": item["bot_id"],
                "used_bytes": item["used_bytes"],
                "limit_bytes": item["limit_bytes"],
                "status": item["status"],
            }
            for item in top
        ],
    }


class WorkspaceQuotaUsageService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler
        self._policy_svc = WorkspaceQuotaPolicyService(handler)

    async def list_usage(
        self,
        *,
        cluster_id: str,
        query: WorkspaceQuotaUsageListQuery,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        cid = cluster_id.strip()
        if not cid:
            raise ValueError("cluster_id is required")

        status_filter = (query.status or "").strip().lower() or None
        if status_filter is not None and status_filter not in _VALID_STATUS:
            raise ValueError("status must be ok, warn, or block")

        await self._sync_from_gateway(
            cluster_id=cid,
            user_id=(query.user_id or "").strip() or None,
            group_id=query.group_id,
            bot_id=(query.bot_id or "").strip() or None,
            actor_id=actor_id,
        )

        filters: dict[str, Any] = {"cluster_id": cid}
        user_id = (query.user_id or "").strip()
        if user_id:
            filters["user_id"] = user_id
        if query.group_id is not None:
            filters["group_id"] = query.group_id
        bot_id = (query.bot_id or "").strip()
        if bot_id:
            filters["bot_id"] = bot_id
        if status_filter:
            filters["status"] = status_filter

        rows = await self._list_all(filters)
        items = [_usage_dict(row) for row in rows]
        search_query = (query.search or "").strip()
        if search_query:
            items = [item for item in items if _matches_usage_search(item, search_query)]
        items.sort(
            key=lambda item: (
                -int(item["used_bytes"]),
                item["user_id"],
                item["group_id"],
                item["bot_id"],
            ),
        )
        summary = _build_summary(items)
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 100)
        total = len(items)
        offset = (page - 1) * page_size
        page_items = items[offset:offset + page_size]
        return {
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "summary": summary,
        }

    async def _sync_from_gateway(
        self,
        *,
        cluster_id: str,
        user_id: str | None,
        group_id: str | None,
        bot_id: str | None,
        actor_id: str | None,
    ) -> None:
        """拉取 Gateway 用量缓存并 upsert。Gateway 不可用时保留本地缓存。"""
        policies = await self._policy_svc.list_enabled(cluster_id=cluster_id)
        params: dict[str, Any] = {"limit": _GATEWAY_PAGE}
        if user_id:
            params["user_id"] = user_id
        if group_id is not None:
            params["group_id"] = group_id
        if bot_id:
            params["bot_id"] = bot_id
        path = f"{_GATEWAY_PATH}?{urlencode(params)}"
        try:
            ack = await gateway_request(cluster_id, "GET", path, {})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "workspace quota usage sync skipped cluster_id=%s: %s",
                cluster_id,
                exc,
            )
            return
        result = ack.get("result") if isinstance(ack, dict) else None
        batch = self._extract_items(result)
        for raw in batch:
            await self._upsert_row(
                cluster_id=cluster_id,
                raw=raw,
                policies=policies,
                actor_id=actor_id,
            )
        if batch:
            logger.info(
                "workspace quota usage synced cluster_id=%s count=%s",
                cluster_id,
                len(batch),
            )

    @staticmethod
    def _extract_items(result: Any) -> list[dict[str, Any]]:
        if isinstance(result, dict):
            items = result.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            return []
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    async def _upsert_row(
        self,
        *,
        cluster_id: str,
        raw: dict[str, Any],
        policies: list[Any],
        actor_id: str | None,
    ) -> None:
        user_id = _as_str(raw.get("user_id")).strip()
        group_id = _as_str(raw.get("group_id"))
        bot_id = _as_str(raw.get("bot_id")).strip()
        if not user_id or not bot_id:
            return
        used_bytes = max(_as_int(raw.get("used_bytes")), 0)
        reported_at = _parse_reported_at(raw.get("reported_at"))

        limit_bytes = raw.get("limit_bytes")
        source_policy_id = _as_str(raw.get("source_policy_id")).strip()
        status = _as_str(raw.get("status")).strip().lower()

        if limit_bytes is None or not source_policy_id or status not in _VALID_STATUS:
            chosen = select_effective_policy(
                policies,
                user_id=user_id,
                group_id=group_id,
                bot_id=bot_id,
            )
            if chosen is None:
                limit_bytes = -1
                source_policy_id = ""
                status = "ok"
            else:
                limit_bytes = _as_int(_g(chosen, "limit_bytes"), -1)
                source_policy_id = _as_str(_g(chosen, "policy_id"))
                status = _usage_status(
                    used_bytes=used_bytes,
                    limit_bytes=limit_bytes,
                    soft_percent=_as_int(_g(chosen, "soft_percent"), 80),
                    hard_percent=_as_int(_g(chosen, "hard_percent"), 100),
                )
        else:
            limit_bytes = _as_int(limit_bytes, -1)

        now = utc_now()
        key = {
            "cluster_id": cluster_id,
            "user_id": user_id,
            "group_id": group_id,
            "bot_id": bot_id,
        }
        existing = await self._handler.get(_TABLE, key)
        payload = {
            "used_bytes": used_bytes,
            "limit_bytes": limit_bytes,
            "source_policy_id": source_policy_id or "unknown",
            "status": status,
            "reported_at": reported_at,
            "updated_at": now,
            "updated_by": actor_id,
        }
        if existing is None:
            await self._handler.create(
                _TABLE,
                {
                    **key,
                    **payload,
                    "data": None,
                    "created_at": now,
                    "created_by": actor_id,
                },
            )
            return
        await self._handler.update(_TABLE, key, payload)

    async def _list_all(self, filters: dict[str, Any]) -> list[Any]:
        rows: list[Any] = []
        offset = 0
        while True:
            batch = await self._handler.list_records(
                _TABLE,
                filters or None,
                limit=_PAGE,
                offset=offset,
            )
            rows.extend(batch)
            if len(batch) < _PAGE:
                return rows
            offset += _PAGE
