"""Team 配额策略：先推 Gateway 再写 Manager。"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.match_expr import validate_match_expr
from manager_server.infrastructure.utils import new_uuid4, utc_now
from manager_server.manager_config_push import gateway_request
from manager_server.models.quota_models import TEAM_QUOTA_POLICY_TABLE_DEF

_TABLE = TEAM_QUOTA_POLICY_TABLE_DEF.table_name
_GATEWAY = "/api/v1/team-quota/policies"


def _g(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _policy_dict(row: Any) -> dict[str, Any]:
    return {
        "cluster_id": _g(row, "cluster_id"),
        "policy_id": _g(row, "policy_id"),
        "policy_name": _g(row, "policy_name"),
        "policy_desc": _g(row, "policy_desc"),
        "match_expr": _g(row, "match_expr"),
        "priority": int(_g(row, "priority", 0) or 0),
        "max_teams_per_user": int(_g(row, "max_teams_per_user", 5) or 5),
        "max_teammates_per_team": int(_g(row, "max_teammates_per_team", 5) or 5),
        "max_teams_per_org": int(_g(row, "max_teams_per_org", 0) or 0),
        "source": _g(row, "source") or "manual",
        "enabled": bool(_g(row, "enabled", True)),
        "created_at": _g(row, "created_at"),
        "updated_at": _g(row, "updated_at"),
    }


def _gateway_body(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": row["policy_id"],
        "policy_name": row["policy_name"],
        "policy_desc": row.get("policy_desc"),
        "match_expr": row.get("match_expr") if row.get("match_expr") is not None else [],
        "priority": int(row.get("priority") or 0),
        "max_teams_per_user": int(row["max_teams_per_user"]),
        "max_teammates_per_team": int(row["max_teammates_per_team"]),
        "max_teams_per_org": int(row.get("max_teams_per_org") or 0),
        "source": row.get("source") or "manual",
        "enabled": bool(row.get("enabled", True)),
    }


class TeamQuotaPolicyService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def list_policies(self, *, cluster_id: str) -> dict[str, Any]:
        cid = cluster_id.strip()
        rows = await self._handler.list_records(_TABLE, {"cluster_id": cid}, limit=500, offset=0)
        items = [_policy_dict(r) for r in rows]
        items.sort(key=lambda r: int(r.get("priority") or 0), reverse=True)
        return {"items": items, "total": len(items)}

    async def upsert(
        self,
        *,
        cluster_id: str,
        policy_id: str | None = None,
        policy_name: str = "team-quota",
        policy_desc: str | None = None,
        match_expr: Any = None,
        priority: int = 0,
        max_teams_per_user: int = 5,
        max_teammates_per_team: int = 5,
        max_teams_per_org: int = 0,
        enabled: bool = True,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        cid = cluster_id.strip()
        if not cid:
            raise ValueError("cluster_id is required")
        x = int(max_teams_per_user)
        y = int(max_teammates_per_team)
        z = int(max_teams_per_org)
        if x < 1 or x > 1000:
            raise ValueError("quota limits must be between 1 and 1000")
        if y < 1 or y > 1000:
            raise ValueError("quota limits must be between 1 and 1000")
        if z < 0 or z > 1000:
            raise ValueError("max_teams_per_org must be between 0 and 1000")
        expr = validate_match_expr(match_expr)
        pid = (policy_id or "").strip() or new_uuid4()
        now = utc_now()
        existing = await self._handler.get(_TABLE, {"policy_id": pid})
        row = {
            "cluster_id": cid,
            "policy_id": pid,
            "policy_name": (policy_name or "team-quota").strip() or "team-quota",
            "policy_desc": (policy_desc or "").strip() or None,
            "match_expr": expr,
            "priority": int(priority),
            "max_teams_per_user": x,
            "max_teammates_per_team": y,
            "max_teams_per_org": z,
            "source": "manual",
            "enabled": bool(enabled),
            "data": None,
            "updated_at": now,
            "updated_by": actor_id,
        }
        try:
            await gateway_request(cid, "PUT", _GATEWAY, _gateway_body(row))
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc
        if existing is None:
            row["created_at"] = now
            row["created_by"] = actor_id
            created = await self._handler.create(_TABLE, row)
            if created is None:
                raise ValueError("failed to create team quota policy")
            return _policy_dict(created)
        if str(_g(existing, "cluster_id") or "") != cid:
            raise ValueError("policy does not belong to this cluster")
        updated = await self._handler.update(_TABLE, {"policy_id": pid}, row)
        return _policy_dict(updated or {**dict(existing), **row})

    async def delete(self, policy_id: str, *, cluster_id: str) -> None:
        pid = policy_id.strip()
        existing = await self._handler.get(_TABLE, {"policy_id": pid})
        if existing is None:
            return
        if str(_g(existing, "cluster_id") or "") != cluster_id.strip():
            raise ValueError("policy not found")
        try:
            await gateway_request(
                cluster_id.strip(),
                "DELETE",
                f"{_GATEWAY}/{pid}",
                {},
            )
        except Exception as exc:
            raise ValueError(f"failed to sync to gateway: {exc}") from exc
        await self._handler.delete(_TABLE, {"policy_id": pid})

    async def list_usage(self, *, cluster_id: str) -> dict[str, Any]:
        cid = cluster_id.strip()
        try:
            data = await gateway_request(cid, "GET", "/api/v1/team-quota/usage", None)
        except Exception as exc:
            raise ValueError(f"failed to read gateway usage: {exc}") from exc
        inner = data.get("result") if isinstance(data, dict) else None
        if isinstance(inner, dict) and "items" in inner:
            return inner
        return {"items": [], "count": 0}
