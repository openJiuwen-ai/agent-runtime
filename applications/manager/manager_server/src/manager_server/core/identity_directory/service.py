"""身份目录只读 service：组织 / 用户 / 用户所属组织反查。

直连 ``identity`` 库的 ``identity_org`` / ``identity_user`` /
``identity_user_org_membership`` 三表（schema 与 identity_center 一致），用
SQLAlchemy 原生查询，不依赖 foundation ORM 注册——manager-server 不应重复
identity_center 的表定义。

handler 为 None（identity 库未配置或初始化失败）时所有方法返回空，调用方降级
为"组织授权不生效 / 选择器空"，不阻断主流程。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from openjiuwen_runtime.foundation.db.handler import DBHandler


def _g(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


class IdentityDirectoryService:
    """身份目录只读访问。handler 为 identity 库的独立句柄。"""

    _CAP = 5_000

    def __init__(self, handler: DBHandler | None) -> None:
        self._h = handler

    def _available(self) -> bool:
        return self._h is not None and getattr(self._h, "session_factory", None) is not None

    async def _session(self) -> AsyncSession | None:
        if not self._available():
            return None
        return self._h.session_factory()  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # 组织目录
    # ------------------------------------------------------------------
    async def list_orgs(
        self, *, search: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """列出组织（group_id/display_name/status），最多 ``_CAP`` 条。"""
        sess = await self._session()
        if sess is None:
            return []
        filters: list[str] = ["group_id != '__none__'"]
        params: dict[str, Any] = {}
        if status:
            filters.append("status = :status")
            params["status"] = status
        if search:
            filters.append("(group_id LIKE :kw OR display_name LIKE :kw)")
            params["kw"] = f"%{search}%"
        sql = (
            "SELECT group_id, display_name, status "
            "FROM identity_org "
            "WHERE " + " AND ".join(filters) + " "
            "ORDER BY display_name ASC, group_id ASC "
            "LIMIT :cap"
        )
        params["cap"] = self._CAP
        async with sess:
            rows = (await sess.execute(text(sql), params)).mappings().all()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 用户目录
    # ------------------------------------------------------------------
    async def list_users(
        self, *, search: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """列出用户（user_id/display_name/is_admin/status）。"""
        sess = await self._session()
        if sess is None:
            return []
        filters: list[str] = []
        params: dict[str, Any] = {}
        if status:
            filters.append("status = :status")
            params["status"] = status
        if search:
            filters.append("(user_id LIKE :kw OR display_name LIKE :kw)")
            params["kw"] = f"%{search}%"
        where = ("WHERE " + " AND ".join(filters) + " ") if filters else ""
        sql = (
            "SELECT user_id, display_name, is_admin, status "
            "FROM identity_user " + where +
            "ORDER BY display_name ASC, user_id ASC "
            "LIMIT :cap"
        )
        params["cap"] = self._CAP
        async with sess:
            rows = (await sess.execute(text(sql), params)).mappings().all()
            return [
                {
                    "user_id": r["user_id"],
                    "display_name": r["display_name"],
                    "is_admin": bool(r["is_admin"]),
                    "status": r["status"],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # 反查：用户所属组织 group_id 列表（skill_upload 组织授权用）
    # ------------------------------------------------------------------
    async def list_user_org_ids(self, user_id: str) -> list[str]:
        """返回用户所属全部组织的 group_id（不含 __none__）。handler 不可用时返回 []。"""
        uid = str(user_id or "").strip()
        if not uid:
            return []
        sess = await self._session()
        if sess is None:
            return []
        sql = (
            "SELECT group_id FROM identity_user_org_membership "
            "WHERE user_id = :uid AND group_id != '__none__'"
        )
        async with sess:
            rows = (await sess.execute(text(sql), {"uid": uid})).mappings().all()
            return [str(r["group_id"]) for r in rows if r["group_id"]]
