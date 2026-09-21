"""工作区配额策略：选路、写入校验，以及先推 Gateway 再写 Manager。"""

from __future__ import annotations

import json
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.match_expr import (
    evaluate_match_expr,
    match_expr_is_unconditional,
    match_key,
    validate_match_expr,
)
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, utc_now
from manager_server.manager_config_push import gateway_request
from manager_server.models.application_config_models import WORKSPACE_QUOTA_POLICY_TABLE_DEF
from manager_server.schemas.application_config_schemas import WorkspaceQuotaPolicyListQuery

_TABLE = WORKSPACE_QUOTA_POLICY_TABLE_DEF.table_name
_GATEWAY_COLLECTION = "/api/v1/workspace-quota/policies"
_PAGE = 500
_ALLOWED_SORT_FIELDS = frozenset({
    "policy_name",
    "policy_desc",
    "priority",
    "match_expr",
    "limit_bytes",
    "soft_percent",
    "hard_percent",
    "source_order_num",
    "updated_at",
})
_DEFAULT_ORDER_BY: list[tuple[str, bool]] = [("priority", False)]
_INT_SORT_FIELDS = frozenset({"priority", "limit_bytes", "soft_percent", "hard_percent"})
_TEXT_SORT_FIELDS = frozenset({"policy_name", "policy_desc", "source_order_num"})


def _g(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _enabled(row: Any) -> bool:
    return bool(_g(row, "enabled", True))


def _is_full_match(expr: Any) -> bool:
    return match_expr_is_unconditional(expr)


def select_effective_policy(
    rows: list[Any],
    *,
    user_id: str,
    group_id: str,
    bot_id: str,
) -> Any | None:
    """同一集群的启用行里，命中且 priority 最小的一条。"""
    hits: list[Any] = []
    for row in rows:
        if not _enabled(row):
            continue
        if evaluate_match_expr(
            _g(row, "match_expr"),
            user_id=user_id,
            group_id=group_id,
            bot_id=bot_id,
        ):
            hits.append(row)
    if not hits:
        return None
    hits.sort(key=lambda row: (int(_g(row, "priority", 0)), str(_g(row, "policy_id") or "")))
    return hits[0]


def _policy_dict(row: Any) -> dict[str, Any]:
    desc = _g(row, "policy_desc")
    if isinstance(desc, str):
        desc = desc.strip() or None
    return {
        "policy_id": _g(row, "policy_id"),
        "cluster_id": _g(row, "cluster_id"),
        "policy_name": str(_g(row, "policy_name") or "").strip(),
        "policy_desc": desc,
        "match_expr": _g(row, "match_expr"),
        "priority": int(_g(row, "priority", 0)),
        "limit_bytes": int(_g(row, "limit_bytes")),
        "soft_percent": int(_g(row, "soft_percent")),
        "hard_percent": int(_g(row, "hard_percent")),
        "source_order_num": _g(row, "source_order_num"),
        "enabled": _enabled(row),
        "created_at": iso_datetime(_g(row, "created_at")),
        "created_by": _g(row, "created_by"),
        "updated_at": iso_datetime(_g(row, "updated_at")),
        "updated_by": _g(row, "updated_by"),
    }


def _gateway_body(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": row["policy_id"],
        "policy_name": row["policy_name"],
        "policy_desc": row["policy_desc"],
        "match_expr": row["match_expr"],
        "priority": row["priority"],
        "limit_bytes": row["limit_bytes"],
        "soft_percent": row["soft_percent"],
        "hard_percent": row["hard_percent"],
        "source_order_num": row["source_order_num"],
        "enabled": row["enabled"],
    }


def _require_percents(soft_percent: int, hard_percent: int) -> None:
    if soft_percent < 0 or hard_percent < 0:
        raise ValueError("soft_percent and hard_percent must be >= 0")
    if hard_percent <= soft_percent:
        raise ValueError("hard_percent must be greater than soft_percent")


def _require_limit(limit_bytes: int) -> None:
    # 0：零配额（不分配空间）；-1：无限制；其余须为正整数。
    if limit_bytes == -1 or limit_bytes >= 0:
        return
    raise ValueError("limit_bytes must be >= 0 or -1 (unlimited)")


def _normalize_policy_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("policy_name is required")
    if len(name) > 128:
        raise ValueError("policy_name must be at most 128 characters")
    return name


def _normalize_policy_desc(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 512:
        raise ValueError("policy_desc must be at most 512 characters")
    return text


def _match_expr_text(expr: Any) -> str:
    """与管理面编辑器一致的匹配范围文本，供搜索和排序。"""
    if expr is None:
        return ""
    if isinstance(expr, list):
        parts = [str(item).strip() for item in expr if str(item).strip()]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return json.dumps(parts, ensure_ascii=False)
    return str(expr).strip()


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(_g(row, "policy_id") or ""),
        str(_g(row, "policy_name") or ""),
        str(_g(row, "policy_desc") or ""),
        _match_expr_text(_g(row, "match_expr")),
        str(_g(row, "source_order_num") or ""),
        str(_g(row, "priority", "")),
        str(_g(row, "limit_bytes", "")),
        str(_g(row, "soft_percent", "")),
        str(_g(row, "hard_percent", "")),
    ]
    return any(needle in field.lower() for field in fields)


def _sort_value(field: str, row: Any) -> Any:
    if field == "match_expr":
        return _match_expr_text(_g(row, "match_expr")).lower()
    if field in _TEXT_SORT_FIELDS:
        return str(_g(row, field) or "").lower()
    if field == "updated_at":
        value = _g(row, "updated_at")
        if value is None:
            return ""
        iso = getattr(value, "isoformat", None)
        return iso() if callable(iso) else str(value)
    if field in _INT_SORT_FIELDS:
        try:
            return int(_g(row, field, 0) or 0)
        except (TypeError, ValueError):
            return 0
    return ""


def _sort_rows(rows: list[Any], sort_by: str | None, sort_order: str | None) -> None:
    order_by = resolve_order_by(
        sort_by,
        sort_order,
        allowed_sort_fields=_ALLOWED_SORT_FIELDS,
        default_order_by=_DEFAULT_ORDER_BY,
    )
    field, is_desc = order_by[0]

    def key(row: Any) -> tuple[Any, str]:
        return (_sort_value(field, row), str(_g(row, "policy_id") or ""))

    rows.sort(key=key, reverse=is_desc)


class WorkspaceQuotaPolicyService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def list_policies(
        self,
        *,
        cluster_id: str,
        query: WorkspaceQuotaPolicyListQuery,
    ) -> dict[str, Any]:
        """按集群列出策略。筛选、排序、分页都在服务内完成；缺省按优先级升序。"""
        filters: dict[str, Any] = {"cluster_id": cluster_id.strip()}
        policy_id = (query.policy_id or "").strip()
        if policy_id:
            filters["policy_id"] = policy_id
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        rows = await self._list_all(filters)
        search_query = (query.search or "").strip()
        if search_query:
            rows = [row for row in rows if _matches_search(row, search_query)]
        _sort_rows(rows, query.sort_by, query.sort_order)
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        total = len(rows)
        offset = (page - 1) * page_size
        page_rows = rows[offset:offset + page_size]
        return {
            "items": [_policy_dict(row) for row in page_rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def effective(
        self,
        *,
        cluster_id: str,
        user_id: str,
        group_id: str,
        bot_id: str,
    ) -> dict[str, Any] | None:
        cid = cluster_id.strip()
        rows = await self._list_all({"cluster_id": cid, "enabled": True})
        chosen = select_effective_policy(
            rows,
            user_id=user_id,
            group_id=group_id,
            bot_id=bot_id,
        )
        if chosen is None:
            return None
        body = _policy_dict(chosen)
        return {
            "cluster_id": cid,
            "user_id": user_id,
            "group_id": group_id,
            "bot_id": bot_id,
            "limit_bytes": body["limit_bytes"],
            "source_policy_id": body["policy_id"],
            "soft_percent": body["soft_percent"],
            "hard_percent": body["hard_percent"],
        }

    async def create(
        self,
        *,
        cluster_id: str,
        policy_name: str,
        policy_desc: str | None = None,
        match_expr: Any = None,
        priority: int = 0,
        limit_bytes: int,
        soft_percent: int = 80,
        hard_percent: int = 100,
        source_order_num: str | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        cid = cluster_id.strip()
        if not cid:
            raise ValueError("cluster_id is required")
        name = _normalize_policy_name(policy_name)
        desc = _normalize_policy_desc(policy_desc)
        expr = validate_match_expr(match_expr)
        _require_limit(limit_bytes)
        _require_percents(soft_percent, hard_percent)
        await self._assert_unique(
            cluster_id=cid,
            match_expr=expr,
            priority=priority,
            enabled=True,
        )
        now = utc_now()
        row = {
            "cluster_id": cid,
            "policy_id": new_uuid4(),
            "policy_name": name,
            "policy_desc": desc,
            "match_expr": expr,
            "priority": int(priority),
            "limit_bytes": int(limit_bytes),
            "soft_percent": int(soft_percent),
            "hard_percent": int(hard_percent),
            "source_order_num": (source_order_num or "").strip() or None,
            "enabled": True,
            "data": None,
            "created_at": now,
            "created_by": actor_id,
            "updated_at": now,
            "updated_by": actor_id,
        }
        await self._push_upsert(row)
        created = await self._handler.create(_TABLE, row)
        if created is None:
            raise ValueError("failed to create workspace quota policy")
        return _policy_dict(created)

    async def update(
        self,
        policy_id: str,
        changes: dict[str, Any],
        *,
        actor_id: str | None = None,
        cluster_id: str | None = None,
    ) -> dict[str, Any] | None:
        pid = policy_id.strip()
        existing = await self._handler.get(_TABLE, {"policy_id": pid})
        if existing is None:
            return None
        if cluster_id is not None and str(_g(existing, "cluster_id") or "") != cluster_id.strip():
            return None
        current = _policy_dict(existing)
        merged = dict(current)
        if "policy_name" in changes:
            merged["policy_name"] = _normalize_policy_name(changes["policy_name"])
        if "policy_desc" in changes:
            merged["policy_desc"] = _normalize_policy_desc(changes["policy_desc"])
        if "match_expr" in changes:
            merged["match_expr"] = validate_match_expr(changes["match_expr"])
        if "priority" in changes:
            merged["priority"] = int(changes["priority"])
        if "limit_bytes" in changes:
            merged["limit_bytes"] = int(changes["limit_bytes"])
            _require_limit(merged["limit_bytes"])
        if "soft_percent" in changes:
            merged["soft_percent"] = int(changes["soft_percent"])
        if "hard_percent" in changes:
            merged["hard_percent"] = int(changes["hard_percent"])
        if "enabled" in changes:
            merged["enabled"] = bool(changes["enabled"])
        _require_percents(merged["soft_percent"], merged["hard_percent"])
        if merged["enabled"]:
            await self._assert_unique(
                cluster_id=merged["cluster_id"],
                match_expr=merged["match_expr"],
                priority=merged["priority"],
                enabled=True,
                exclude_policy_id=pid,
            )
        now = utc_now()
        merged["updated_at"] = now
        merged["updated_by"] = actor_id
        await self._push_upsert(merged)
        updated = await self._handler.update(
            _TABLE,
            {"policy_id": pid},
            {
                "policy_name": merged["policy_name"],
                "policy_desc": merged["policy_desc"],
                "match_expr": merged["match_expr"],
                "priority": merged["priority"],
                "limit_bytes": merged["limit_bytes"],
                "soft_percent": merged["soft_percent"],
                "hard_percent": merged["hard_percent"],
                "enabled": merged["enabled"],
                "updated_at": now,
                "updated_by": actor_id,
            },
        )
        if updated is None:
            raise ValueError("failed to update workspace quota policy")
        return _policy_dict(updated)

    async def delete(self, policy_id: str, *, cluster_id: str | None = None) -> None:
        pid = policy_id.strip()
        existing = await self._handler.get(_TABLE, {"policy_id": pid})
        if existing is None:
            raise ValueError("workspace quota policy not found")
        if cluster_id is not None and str(_g(existing, "cluster_id") or "") != cluster_id.strip():
            raise ValueError("workspace quota policy not found")
        cluster_id = str(_g(existing, "cluster_id") or "")
        await self._push_delete(cluster_id, pid)
        deleted = await self._handler.delete(_TABLE, {"policy_id": pid})
        if not deleted:
            raise ValueError("failed to delete workspace quota policy")

    async def _assert_unique(
        self,
        *,
        cluster_id: str,
        match_expr: Any,
        priority: int,
        enabled: bool,
        exclude_policy_id: str | None = None,
    ) -> None:
        if not enabled:
            return
        rows = await self._list_all({"cluster_id": cluster_id, "enabled": True})
        key = match_key(match_expr)
        full = _is_full_match(match_expr)
        for row in rows:
            pid = str(_g(row, "policy_id") or "")
            if exclude_policy_id and pid == exclude_policy_id:
                continue
            if int(_g(row, "priority", 0)) == int(priority):
                raise ValueError("priority already used by an enabled policy in this cluster")
            if match_key(_g(row, "match_expr")) == key:
                raise ValueError("match_expr already used by an enabled policy in this cluster")
            if full and _is_full_match(_g(row, "match_expr")):
                raise ValueError("full-match policy already exists in this cluster")

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

    async def _push_upsert(self, row: dict[str, Any]) -> None:
        try:
            await gateway_request(
                row["cluster_id"],
                "PUT",
                _GATEWAY_COLLECTION,
                _gateway_body(row),
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc

    async def _push_delete(self, cluster_id: str, policy_id: str) -> None:
        try:
            await gateway_request(
                cluster_id,
                "DELETE",
                f"{_GATEWAY_COLLECTION}/{policy_id}",
                {},
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc
