# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""身份变更后的团队级联清理：删组织 / 删用户 / 用户移出组织。"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.manager_config_push import gateway_request
from manager_server.routers.auth_guards import require_admin
from manager_server.schemas.common_schemas import ResponseModel

org_cleanup_router = APIRouter()

_INSTANCE_TABLE = "instance_info"
_CAP = 1000
_LOG = logging.getLogger(__name__)


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def _identity_base_url() -> str:
    public_key = str(settings.identity_public_key_url or "").rstrip("/")
    suffix = "/v1/auth/public_key"
    if public_key.endswith(suffix):
        return public_key[: -len(suffix)]
    return str(settings.manager_web_idp_target or "").rstrip("/")


async def _user_org_groups(
    user_id: str, authorization: str | None
) -> list[str] | None:
    """用户当前组织；用户已不存在返回 ``[]``；身份服务不可用返回 ``None``。

    ``None`` 让调用方放弃级联，避免把“读不到”误当成“用户没有任何组织”而清空额度。
    """
    token = (authorization or "").strip()
    base = _identity_base_url()
    if not token or not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            response = await client.get(
                f"{base}/v1/users/{quote(user_id, safe='')}/orgs",
                headers={"Authorization": token},
            )
    except httpx.HTTPError as exc:
        _LOG.warning("read user orgs failed: %s", exc)
        return None
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        return None
    body = response.json()
    raw = body.get("group_ids") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        return None
    return [str(g).strip() for g in raw if str(g).strip()]


async def _fan_out(
    handler: DBHandler, path: str, body: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = await handler.list_records(_INSTANCE_TABLE, {}, limit=_CAP, offset=0)
    clusters: list[dict[str, Any]] = []
    for row in rows:
        cid = str(_g(row, "jiuwenclaw_id") or "").strip()
        if not cid:
            continue
        try:
            await gateway_request(cid, "POST", path, body, timeout=60.0)
            clusters.append({"cluster_id": cid, "ok": True})
        except Exception as exc:  # noqa: BLE001
            clusters.append({"cluster_id": cid, "ok": False, "error": str(exc)})
    return clusters


@org_cleanup_router.post("/orgs/{group_id}/reclaim-teams", response_model=ResponseModel)
async def reclaim_org_teams(
    group_id: str,
    user: Annotated[Any, Depends(require_admin)],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    """停止所有集群中归属该组织的存活团队，并释放其配额。"""
    gid = group_id.strip()
    if not gid:
        raise HTTPException(status_code=400, detail="group_id is required")
    clusters = await _fan_out(
        handler, f"/api/v1/team-quota/orgs/{quote(gid, safe='')}/stop", {}
    )
    return ResponseModel(
        code=200,
        message="success",
        data={"group_id": gid, "clusters": clusters},
    )


@org_cleanup_router.post("/users/{user_id}/reclaim-teams", response_model=ResponseModel)
async def reclaim_user_teams(
    user_id: str,
    user: Annotated[Any, Depends(require_admin)],
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    authorization: Annotated[str | None, Header()] = None,
):
    """停止用户已不再归属的组织的团队。删用户时为空保留集合，停其全部团队。"""
    uid = user_id.strip()
    if not uid:
        raise HTTPException(status_code=400, detail="user_id is required")
    keep = await _user_org_groups(uid, authorization)
    if keep is None:
        raise HTTPException(status_code=503, detail="identity directory unavailable")
    clusters = await _fan_out(
        handler,
        f"/api/v1/team-quota/users/{quote(uid, safe='')}/stop",
        {"keep_group_ids": keep},
    )
    return ResponseModel(
        code=200,
        message="success",
        data={"user_id": uid, "keep_group_ids": keep, "clusters": clusters},
    )
