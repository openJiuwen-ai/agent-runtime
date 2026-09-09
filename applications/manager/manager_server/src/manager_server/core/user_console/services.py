"""用户控制台业务逻辑：当前用户可访问的 Agent 上下文组合。

instance_grant 在 core/instance_access；instance_agent_resource 在 core/instance_resource。
返回 (bot_id, group_id, user_id) 组合；任选其一即可访问对应 Agent。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance_access import InstanceGrantService
from manager_server.core.instance_access.instance_grant_service import SUBJECT_ORG, SUBJECT_USER
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.match_expr import evaluate_match_expr
from manager_server.infrastructure.utils import utc_now
from manager_server.models.instance_resource_models import INSTANCE_AGENT_RESOURCE_TABLE_DEF

_GRANT = INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name
_CAP = 100_000
_NO_ORG_GROUP_ID = "__none__"


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def _grant_expired(expires_at: Any) -> bool:
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= utc_now()


def _identity_base_url() -> str:
    public_key = str(settings.identity_public_key_url or "").rstrip("/")
    suffix = "/v1/auth/public_key"
    if public_key.endswith(suffix):
        return public_key[: -len(suffix)]
    return str(settings.manager_web_idp_target or "").rstrip("/")


async def _load_orgs(authorization: str | None) -> list[dict[str, str]]:
    """一次拉取用户组织：每项含 group_id + group_name（display_name）。失败返回空。"""
    token = (authorization or "").strip()
    base = _identity_base_url()
    if not token or not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(
                f"{base}/v1/auth/me/orgs",
                headers={"Authorization": token},
            )
        if resp.status_code != 200:
            return []
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    raw = body.get("orgs") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("group_id") or "").strip()
        if not gid:
            continue
        name = str(item.get("display_name") or item.get("name") or "").strip() or gid
        out.append({"group_id": gid, "group_name": name})
    return out


class UserConsoleService:
    """按 instance_grant + instance_agent_resource 枚举当前用户可访问的上下文。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler
        self._instance_grants = InstanceGrantService(handler)

    async def list_accessible_contexts(
        self,
        user_id: str,
        groups: list[str] | None = None,
        *,
        is_admin: bool = False,
        authorization: str | None = None,
    ) -> list[dict[str, Any]]:
        """返回用户可选用的上下文列表。

        业务键均为 id：``bot_id`` / ``group_id`` / ``user_id``。
        展示字段：``agent_name``（资源表）/ ``group_name``（身份中心，与组织列表同次查询得到）。
        """
        uid = str(user_id or "").strip()
        if not uid:
            return []

        jwt_groups = {
            str(item).strip() for item in (groups or []) if str(item).strip()
        }
        # 先查组织目录（id + 名称一次齐）；与 JWT groups 求交，保证授权仍以 token 为准。
        orgs = await _load_orgs(authorization)
        if orgs and jwt_groups:
            orgs = [org for org in orgs if org["group_id"] in jwt_groups]
        if orgs:
            candidate_groups = [(org["group_id"], org["group_name"]) for org in orgs]
            member_groups = {gid for gid, _ in candidate_groups}
        elif jwt_groups:
            candidate_groups = [(gid, gid) for gid in sorted(jwt_groups)]
            member_groups = jwt_groups
        else:
            candidate_groups = [(_NO_ORG_GROUP_ID, "无组织")]
            member_groups = set()

        admitted = await self._admitted_instance_ids(uid, member_groups, is_admin=is_admin)
        if not admitted:
            return []

        seen: set[tuple[str, str, str]] = set()
        out: list[dict[str, Any]] = []
        for jid in sorted(admitted):
            rows = await self._h.list_records(
                _GRANT, {"jiuwenclaw_id": jid}, limit=_CAP, offset=0
            )
            for r in rows:
                if not bool(_g(r, "enabled", True)) or _grant_expired(_g(r, "expires_at")):
                    continue
                bot_id = str(_g(r, "resource_id") or "").strip()
                if not bot_id:
                    continue
                expr = _g(r, "match_expr")
                agent_name = str(_g(r, "resource_name") or "").strip() or bot_id
                for group_id, group_name in candidate_groups:
                    if not (
                        is_admin
                        or evaluate_match_expr(
                            expr, user_id=uid, group_id=group_id, bot_id=""
                        )
                    ):
                        continue
                    key = (bot_id, group_id, uid)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        {
                            "bot_id": bot_id,
                            "group_id": group_id,
                            "user_id": uid,
                            "jiuwenclaw_id": jid,
                            "agent_name": agent_name,
                            "group_name": group_name,
                        }
                    )
        out.sort(
            key=lambda x: (
                str(x.get("agent_name") or ""),
                str(x.get("group_name") or ""),
                str(x.get("bot_id") or ""),
                str(x.get("group_id") or ""),
            )
        )
        return out

    async def _admitted_instance_ids(
        self, user_id: str, member_groups: set[str], *, is_admin: bool
    ) -> set[str]:
        if is_admin:
            rows = await self._h.list_records("instance_info", {}, limit=_CAP, offset=0)
            return {
                str(_g(r, "jiuwenclaw_id") or "").strip()
                for r in rows
                if str(_g(r, "jiuwenclaw_id") or "").strip()
            }

        allowed: set[str] = set()
        user_map = await self._instance_grants.list_instances_for(SUBJECT_USER, [user_id])
        allowed.update(user_map.get(user_id, []))
        if member_groups:
            org_map = await self._instance_grants.list_instances_for(
                SUBJECT_ORG, list(member_groups)
            )
            for ids in org_map.values():
                allowed.update(ids)
        result: set[str] = set()
        for jid in allowed:
            if await self._instance_grants.is_admitted(jid, user_id, member_groups):
                result.add(jid)
        return result


__all__ = ("UserConsoleService",)
