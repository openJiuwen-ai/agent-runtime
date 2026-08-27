"""实例准入绑定 instance_grant（合并原 user_gateway / org_gateway）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.match_expr import iter_equality_binds
from manager_server.infrastructure.utils import iso_datetime, strip_optional, utc_now
from manager_server.models.instance_access_models import INSTANCE_GRANT_TABLE_DEF

_TABLE = INSTANCE_GRANT_TABLE_DEF.table_name
_CAP = 100_000

SUBJECT_USER = "user"
SUBJECT_ORG = "org"

LOGIN_POLICY_ALLOW = "allow"
LOGIN_POLICY_DENY = "deny"
LoginPolicy = Literal["allow", "deny"]
_VALID_LOGIN_POLICIES = frozenset({LOGIN_POLICY_ALLOW, LOGIN_POLICY_DENY})


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def normalize_login_policy(value: Any, *, default: str = LOGIN_POLICY_ALLOW) -> str:
    raw = str(value if value is not None else default).strip().lower()
    if raw not in _VALID_LOGIN_POLICIES:
        raise ValueError(f"login_policy must be one of {sorted(_VALID_LOGIN_POLICIES)}")
    return raw


def _login_policy_of(row: Any) -> str:
    try:
        return normalize_login_policy(_g(row, "login_policy"), default=LOGIN_POLICY_ALLOW)
    except ValueError:
        return LOGIN_POLICY_ALLOW


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


def _active(row: Any) -> bool:
    return bool(_g(row, "enabled", True)) and not _grant_expired(_g(row, "expires_at"))


def grant_out(row: Any) -> dict[str, Any]:
    return {
        "id": _g(row, "id"),
        "jiuwenclaw_id": _g(row, "jiuwenclaw_id"),
        "subject_type": _g(row, "subject_type"),
        "subject_id": _g(row, "subject_id"),
        "granted_by": _g(row, "granted_by"),
        "login_policy": _login_policy_of(row),
        "expires_at": iso_datetime(_g(row, "expires_at")),
        "enabled": bool(_g(row, "enabled", True)),
        "data": _g(row, "data"),
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }


async def ensure_instance_grant(
    handler: DBHandler,
    jiuwenclaw_id: str,
    subject_type: str,
    subject_id: str,
    *,
    granted_by: str | None = None,
    login_policy: str = LOGIN_POLICY_ALLOW,
    expires_at: datetime | None = None,
    enabled: bool = True,
) -> None:
    """若不存在则插入一条 instance_grant。"""
    sid = str(subject_id).strip()
    if not sid:
        return
    policy = normalize_login_policy(login_policy)
    filters = {
        "jiuwenclaw_id": jiuwenclaw_id,
        "subject_type": subject_type,
        "subject_id": sid,
    }
    if await handler.get(_TABLE, filters) is not None:
        return
    now = utc_now()
    await handler.create(
        _TABLE,
        {
            **filters,
            "granted_by": strip_optional(granted_by),
            "login_policy": policy,
            "expires_at": expires_at,
            "enabled": enabled,
            "data": None,
            "created_at": now,
            "updated_at": now,
        },
    )


async def auto_bind_from_match_expr(handler: DBHandler, jiuwenclaw_id: str, expr: Any) -> None:
    """从 match_expr 中的 user_id / group_id 等式绑定自动写入 instance_grant。"""
    for name, value in iter_equality_binds(expr):
        if name == "group_id":
            await ensure_instance_grant(handler, jiuwenclaw_id, SUBJECT_ORG, value)
        elif name == "user_id":
            await ensure_instance_grant(handler, jiuwenclaw_id, SUBJECT_USER, value)


class InstanceGrantService:
    """instance_grant：实例准入绑定。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def list_grants(self, jiuwenclaw_id: str, subject_type: str) -> list[dict[str, Any]]:
        rows = await self._h.list_records(
            _TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id, "subject_type": subject_type},
            limit=_CAP,
            offset=0,
        )
        return [grant_out(r) for r in rows]

    async def list_subject_ids(self, jiuwenclaw_id: str, subject_type: str) -> list[str]:
        return [str(g["subject_id"]) for g in await self.list_grants(jiuwenclaw_id, subject_type)]

    async def bind(
        self,
        jiuwenclaw_id: str,
        subject_type: str,
        entity_ids: list[str],
        *,
        granted_by: str | None = None,
        login_policy: str = LOGIN_POLICY_ALLOW,
        expires_at: datetime | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        policy = normalize_login_policy(login_policy)
        added, skipped = [], []
        for raw in entity_ids:
            eid = str(raw).strip()
            if not eid:
                continue
            filters = {
                "jiuwenclaw_id": jiuwenclaw_id,
                "subject_type": subject_type,
                "subject_id": eid,
            }
            if await self._h.get(_TABLE, filters) is not None:
                skipped.append(eid)
                continue
            await ensure_instance_grant(
                self._h,
                jiuwenclaw_id,
                subject_type,
                eid,
                granted_by=granted_by,
                login_policy=policy,
                expires_at=expires_at,
                enabled=enabled,
            )
            added.append(eid)
        return {"added": added, "skipped": skipped}

    async def update_grant(
        self,
        jiuwenclaw_id: str,
        subject_type: str,
        subject_id: str,
        *,
        enabled: bool | None = None,
        login_policy: str | None = None,
        expires_at: datetime | None = None,
        clear_expires_at: bool = False,
        granted_by: str | None = None,
    ) -> dict[str, Any] | None:
        sid = str(subject_id).strip()
        row = await self._h.get(
            _TABLE,
            {
                "jiuwenclaw_id": jiuwenclaw_id,
                "subject_type": subject_type,
                "subject_id": sid,
            },
        )
        if row is None:
            return None
        patch: dict[str, Any] = {"updated_at": utc_now()}
        if enabled is not None:
            patch["enabled"] = enabled
        if login_policy is not None:
            patch["login_policy"] = normalize_login_policy(login_policy)
        if clear_expires_at:
            patch["expires_at"] = None
        elif expires_at is not None:
            patch["expires_at"] = expires_at
        if granted_by is not None:
            patch["granted_by"] = strip_optional(granted_by)
        await self._h.update(_TABLE, {"id": _g(row, "id")}, patch)
        updated = await self._h.get(_TABLE, {"id": _g(row, "id")})
        return grant_out(updated) if updated is not None else None

    async def unbind(
        self, jiuwenclaw_id: str, subject_type: str, entity_ids: list[str]
    ) -> dict[str, Any]:
        removed = []
        for raw in entity_ids:
            eid = str(raw).strip()
            if not eid:
                continue
            rows = await self._h.list_records(
                _TABLE,
                {
                    "jiuwenclaw_id": jiuwenclaw_id,
                    "subject_type": subject_type,
                    "subject_id": eid,
                },
                limit=_CAP,
                offset=0,
            )
            for r in rows:
                await self._h.delete(_TABLE, {"id": _g(r, "id")})
            if rows:
                removed.append(eid)
        return {"removed": removed}

    async def list_instances_for(
        self, subject_type: str, entity_ids: list[str]
    ) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {str(e).strip(): [] for e in entity_ids if str(e).strip()}
        if not out:
            return out
        for r in await self._h.list_records(
            _TABLE, {"subject_type": subject_type}, limit=_CAP, offset=0
        ):
            if not _active(r):
                continue
            eid = str(_g(r, "subject_id"))
            if eid in out:
                out[eid].append(str(_g(r, "jiuwenclaw_id")))
        return out

    async def is_admitted(
        self, jiuwenclaw_id: str, user_id: str, member_groups: set[str]
    ) -> bool:
        """准入：命中启用且未过期的绑定；deny 优先于 allow。"""
        matched: list[Any] = []
        user_row = await self._h.get(
            _TABLE,
            {
                "jiuwenclaw_id": jiuwenclaw_id,
                "subject_type": SUBJECT_USER,
                "subject_id": user_id,
            },
        )
        if user_row is not None and _active(user_row):
            matched.append(user_row)
        for gid in member_groups:
            org_row = await self._h.get(
                _TABLE,
                {
                    "jiuwenclaw_id": jiuwenclaw_id,
                    "subject_type": SUBJECT_ORG,
                    "subject_id": gid,
                },
            )
            if org_row is not None and _active(org_row):
                matched.append(org_row)
        if not matched:
            return False
        if any(_login_policy_of(r) == LOGIN_POLICY_DENY for r in matched):
            return False
        return any(_login_policy_of(r) == LOGIN_POLICY_ALLOW for r in matched)


class GatewayBindingService:
    """固定 subject_type 的 instance_grant 视图（兼容原 user/org gateway API）。"""

    def __init__(self, handler: DBHandler, subject_type: str) -> None:
        self._svc = InstanceGrantService(handler)
        self._subject_type = subject_type

    async def list_members(self, jiuwenclaw_id: str) -> list[str]:
        return await self._svc.list_subject_ids(jiuwenclaw_id, self._subject_type)

    async def list_grants(self, jiuwenclaw_id: str) -> list[dict[str, Any]]:
        return await self._svc.list_grants(jiuwenclaw_id, self._subject_type)

    async def bind(
        self,
        jiuwenclaw_id: str,
        entity_ids: list[str],
        *,
        granted_by: str | None = None,
        login_policy: str = LOGIN_POLICY_ALLOW,
        expires_at: datetime | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        return await self._svc.bind(
            jiuwenclaw_id,
            self._subject_type,
            entity_ids,
            granted_by=granted_by,
            login_policy=login_policy,
            expires_at=expires_at,
            enabled=enabled,
        )

    async def update_grant(
        self,
        jiuwenclaw_id: str,
        subject_id: str,
        *,
        enabled: bool | None = None,
        login_policy: str | None = None,
        expires_at: datetime | None = None,
        clear_expires_at: bool = False,
        granted_by: str | None = None,
    ) -> dict[str, Any] | None:
        return await self._svc.update_grant(
            jiuwenclaw_id,
            self._subject_type,
            subject_id,
            enabled=enabled,
            login_policy=login_policy,
            expires_at=expires_at,
            clear_expires_at=clear_expires_at,
            granted_by=granted_by,
        )

    async def unbind(self, jiuwenclaw_id: str, entity_ids: list[str]) -> dict[str, Any]:
        return await self._svc.unbind(jiuwenclaw_id, self._subject_type, entity_ids)

    async def list_instances_for(self, entity_ids: list[str]) -> dict[str, list[str]]:
        return await self._svc.list_instances_for(self._subject_type, entity_ids)


def user_gateway_service(handler: DBHandler) -> GatewayBindingService:
    return GatewayBindingService(handler, SUBJECT_USER)


def org_gateway_service(handler: DBHandler) -> GatewayBindingService:
    return GatewayBindingService(handler, SUBJECT_ORG)
