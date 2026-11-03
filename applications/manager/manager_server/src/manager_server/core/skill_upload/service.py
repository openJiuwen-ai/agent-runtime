"""Skill 上传授权：管理员直授与审批共用的唯一授权记录与检查逻辑。

审批状态在 ``approval_order.status``；本模块只维护授权记录自身的
``enabled``（设计文档 §10.2：``enabled=true`` 生效 / ``enabled=false`` 不生效）
与有效期，两者互不覆盖。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.log import get_logger

from manager_server.infrastructure.utils import iso_datetime, utc_now
from manager_server.models.skill_upload_models import SKILL_UPLOAD_PERMISSION_TABLE_DEF
from manager_server.schemas.skill_upload_schemas import SkillUploadGrantListQuery

_TABLE = SKILL_UPLOAD_PERMISSION_TABLE_DEF.table_name
_CAP = 100_000

SUBJECT_USER = "user"
SUBJECT_ORG = "org"
_VALID_SUBJECTS = frozenset({SUBJECT_USER, SUBJECT_ORG})

SOURCE_ADMIN = "admin"
SOURCE_APPROVAL = "approval"
_VALID_SOURCES = frozenset({SOURCE_ADMIN, SOURCE_APPROVAL})

_log = get_logger(__name__)


def _g(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalize_expires_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if value <= utc_now():
        raise ValueError("expires_at must be in the future")
    return value


def _expired(expires_at: Any) -> bool:
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


def _truthy(value: Any) -> bool:
    """容忍底层 handler 把布尔落成 0/1/字符串的兼容读取。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _enabled(row: Any) -> bool:
    return _truthy(_g(row, "enabled")) and not _expired(_g(row, "expires_at"))


def _find_user_grant(rows: list[Any], user_id: str) -> Any | None:
    """从已启用的授权行中查找指定用户的直授记录。"""
    for row in rows:
        if _g(row, "subject_type") == SUBJECT_USER and str(_g(row, "subject_id")) == user_id:
            return row
    return None


def _find_org_grant(rows: list[Any], member_groups: set[str]) -> Any | None:
    """从已启用的授权行中查找组织授权记录(匹配用户所属组织集合)。"""
    for row in rows:
        if _g(row, "subject_type") == SUBJECT_ORG and str(_g(row, "subject_id")) in member_groups:
            return row
    return None


def permission_out(row: Any) -> dict[str, Any]:
    enabled = _enabled(row)
    return {
        "id": _g(row, "id"),
        "cluster_id": _g(row, "cluster_id"),
        "subject_type": _g(row, "subject_type"),
        "subject_id": _g(row, "subject_id"),
        "source": _g(row, "source") or SOURCE_ADMIN,
        "source_order_num": _g(row, "source_order_num"),
        "enabled": enabled,
        "granted_by": _g(row, "granted_by"),
        "expires_at": iso_datetime(_g(row, "expires_at")),
        "updated_by": _g(row, "updated_by"),
        "updated_at": iso_datetime(_g(row, "updated_at")),
        "data": _g(row, "data"),
        "created_at": iso_datetime(_g(row, "created_at")),
    }


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(_g(row, "subject_id", "") or ""),
        str(_g(row, "granted_by", "") or ""),
        str(_g(row, "source_order_num", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


class SkillUploadPermissionService:
    """Skill 上传授权的读写与统一准入判定。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    async def list_permissions(
        self, query: SkillUploadGrantListQuery | None = None
    ) -> dict[str, Any]:
        query = query or SkillUploadGrantListQuery()
        filters: dict[str, Any] = {}
        if query.cluster_id:
            filters["cluster_id"] = query.cluster_id
        if query.subject_type:
            filters["subject_type"] = query.subject_type
        if query.source:
            filters["source"] = query.source
        # enabled 不能直接作为等值过滤（底层 handler 对布尔的存储/读取差异），
        # 由 Python 侧统一判定，避免 0/1/false 字符串等不一致。
        rows = await self._h.list_records(_TABLE, filters, limit=_CAP, offset=0)
        if query.enabled is not None:
            rows = [row for row in rows if _enabled(row) == query.enabled]
        if query.search and query.search.strip():
            rows = [row for row in rows if _matches_search(row, query.search)]
        items = sorted(
            (permission_out(row) for row in rows),
            key=lambda item: (
                str(item.get("subject_type") or ""),
                str(item.get("subject_id") or ""),
            ),
        )
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        offset = (page - 1) * page_size
        return {
            "items": items[offset:offset + page_size],
            "total": len(items),
            "page": page,
            "page_size": page_size,
        }

    async def get_permission(self, permission_id: int) -> dict[str, Any] | None:
        row = await self._h.get(_TABLE, {"id": permission_id})
        return None if row is None else permission_out(row)

    # ------------------------------------------------------------------
    # 写入（管理员直授）
    # ------------------------------------------------------------------
    async def bind(
        self,
        *,
        cluster_id: str,
        subject_type: str,
        ids: list[str],
        operator_id: str,
        expires_at: datetime | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if subject_type not in _VALID_SUBJECTS:
            raise ValueError(f"subject_type must be one of {sorted(_VALID_SUBJECTS)}")
        cid = str(cluster_id or "").strip()
        if not cid:
            raise ValueError("cluster_id is required")
        targets = sorted({str(item).strip() for item in ids if str(item).strip()})
        if not targets:
            raise ValueError("ids is required")
        normalized_expires = _normalize_expires_at(expires_at)
        now = utc_now()
        created: list[str] = []
        updated: list[str] = []
        for subject_id in targets:
            filters = {
                "cluster_id": cid,
                "subject_type": subject_type,
                "subject_id": subject_id,
            }
            existing = await self._h.get(_TABLE, filters)
            values = {
                "source": SOURCE_ADMIN,
                "source_order_num": None,
                "enabled": bool(enabled),
                "granted_by": operator_id,
                "expires_at": normalized_expires,
                "updated_at": now,
                "updated_by": operator_id,
            }
            if existing is None:
                await self._h.create(
                    _TABLE,
                    {
                        **filters,
                        **values,
                        "data": None,
                        "created_at": now,
                        "created_by": operator_id,
                    },
                )
                created.append(subject_id)
            else:
                await self._h.update(_TABLE, filters, values)
                updated.append(subject_id)
        _log.info(
            "skill upload permission changed operator_id=%s cluster_id=%s subject_type=%s "
            "enabled=%s created=%s updated=%s",
            operator_id,
            cid,
            subject_type,
            enabled,
            created,
            updated,
        )
        return {"cluster_id": cid, "subject_type": subject_type, "created": created, "updated": updated}

    async def update_permission(
        self,
        permission_id: int,
        *,
        enabled: bool | None,
        expires_at: datetime | None,
        clear_expires_at: bool,
        operator_id: str,
    ) -> dict[str, Any] | None:
        row = await self._h.get(_TABLE, {"id": permission_id})
        if row is None:
            return None
        updates: dict[str, Any] = {"updated_at": utc_now(), "updated_by": operator_id}
        if enabled is not None:
            updates["enabled"] = bool(enabled)
        if clear_expires_at:
            updates["expires_at"] = None
        elif expires_at is not None:
            updates["expires_at"] = _normalize_expires_at(expires_at)
        await self._h.update(_TABLE, {"id": permission_id}, updates)
        return await self.get_permission(permission_id)

    async def revoke(
        self, *, cluster_id: str, subject_type: str, ids: list[str], operator_id: str
    ) -> dict[str, Any]:
        if subject_type not in _VALID_SUBJECTS:
            raise ValueError(f"subject_type must be one of {sorted(_VALID_SUBJECTS)}")
        cid = str(cluster_id or "").strip()
        if not cid:
            raise ValueError("cluster_id is required")
        targets = sorted({str(item).strip() for item in ids if str(item).strip()})
        if not targets:
            raise ValueError("ids is required")
        revoked: list[str] = []
        now = utc_now()
        for subject_id in targets:
            filters = {
                "cluster_id": cid,
                "subject_type": subject_type,
                "subject_id": subject_id,
            }
            if await self._h.get(_TABLE, filters) is None:
                continue
            await self._h.update(
                _TABLE,
                filters,
                {
                    "enabled": False,
                    "updated_at": now,
                    "updated_by": operator_id,
                },
            )
            revoked.append(subject_id)
        _log.info(
            "skill upload permission revoked operator_id=%s cluster_id=%s subject_type=%s revoked=%s",
            operator_id,
            cid,
            subject_type,
            revoked,
        )
        return {"cluster_id": cid, "subject_type": subject_type, "revoked": revoked}

    # ------------------------------------------------------------------
    # 检查（唯一准入逻辑）
    # ------------------------------------------------------------------
    async def effective(
        self, *, cluster_id: str, user_id: str, groups: list[str] | None = None
    ) -> dict[str, Any]:
        """返回当前用户的上传资格解析结果；用户直授优先于组织授权。"""
        uid = str(user_id or "").strip()
        cid = str(cluster_id or "").strip()
        member_groups = {
            str(item).strip() for item in (groups or []) if str(item).strip()
        }
        result: dict[str, Any] = {
            "allowed": False,
            "cluster_id": cid,
            "user_id": uid,
            "via": None,
            "permission": None,
            "groups": sorted(member_groups),
        }
        if not uid or not cid:
            return result
        rows = await self._h.list_records(
            _TABLE, {"cluster_id": cid}, limit=_CAP, offset=0
        )
        active_rows = [row for row in rows if _enabled(row)]
        direct = _find_user_grant(active_rows, uid)
        if direct is not None:
            result.update(
                {
                    "allowed": True,
                    "via": SUBJECT_USER,
                    "permission": permission_out(direct),
                }
            )
            return result
        org = _find_org_grant(active_rows, member_groups)
        if org is not None:
            result.update(
                {"allowed": True, "via": SUBJECT_ORG, "permission": permission_out(org)}
            )
        return result

    async def is_allowed(
        self, *, cluster_id: str, user_id: str, groups: list[str] | None = None
    ) -> bool:
        return bool(
            (await self.effective(cluster_id=cluster_id, user_id=user_id, groups=groups)).get("allowed")
        )

    # ------------------------------------------------------------------
    # 审批通过后写入统一授权记录
    # ------------------------------------------------------------------
    async def upsert_from_approval(
        self,
        *,
        cluster_id: str,
        user_id: str,
        order_num: str,
        operator_id: str,
    ) -> dict[str, Any]:
        uid = str(user_id or "").strip()
        if not uid:
            raise ValueError("applicant user_id is required")
        cid = str(cluster_id or "").strip()
        if not cid:
            raise ValueError("cluster_id is required")
        now = utc_now()
        filters = {
            "cluster_id": cid,
            "subject_type": SUBJECT_USER,
            "subject_id": uid,
        }
        existing = await self._h.get(_TABLE, filters)
        values = {
            "source": SOURCE_APPROVAL,
            "source_order_num": order_num,
            "enabled": True,
            "granted_by": operator_id,
            "expires_at": None,
            "updated_at": now,
            "updated_by": operator_id,
        }
        if existing is None:
            await self._h.create(
                _TABLE,
                {
                    **filters,
                    **values,
                    "data": None,
                    "created_at": now,
                    "created_by": operator_id,
                },
            )
        else:
            await self._h.update(_TABLE, filters, values)
        row = await self._h.get(_TABLE, filters)
        return permission_out(row) if row is not None else {}


__all__ = (
    "SOURCE_ADMIN",
    "SOURCE_APPROVAL",
    "SUBJECT_ORG",
    "SUBJECT_USER",
    "SkillUploadPermissionService",
    "permission_out",
)
