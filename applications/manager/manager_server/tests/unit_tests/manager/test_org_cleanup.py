# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""身份变更后的跨集群团队清理。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_MOD = "manager_server.routers.org_cleanup_routers"


def _handler(*cluster_ids: str) -> MagicMock:
    handler = MagicMock()
    handler.list_records = AsyncMock(
        return_value=[SimpleNamespace(jiuwenclaw_id=cid) for cid in cluster_ids]
    )
    return handler


@pytest.mark.asyncio
async def test_reclaim_org_teams_fans_out_to_clusters():
    from manager_server.routers.org_cleanup_routers import reclaim_org_teams

    handler = _handler("cluster_01", "cluster_02")
    gateway = AsyncMock(side_effect=[None, RuntimeError("offline")])
    with patch(f"{_MOD}.gateway_request", gateway):
        response = await reclaim_org_teams("grp2", user=object(), handler=handler)

    assert response.data["group_id"] == "grp2"
    assert response.data["clusters"] == [
        {"cluster_id": "cluster_01", "ok": True},
        {"cluster_id": "cluster_02", "ok": False, "error": "offline"},
    ]
    assert gateway.await_args_list[0].args[2] == "/api/v1/team-quota/orgs/grp2/stop"
    assert gateway.await_args_list[0].args[3] == {}


@pytest.mark.asyncio
async def test_reclaim_org_teams_requires_group_id():
    from fastapi import HTTPException

    from manager_server.routers.org_cleanup_routers import reclaim_org_teams

    with pytest.raises(HTTPException) as exc:
        await reclaim_org_teams(" ", user=object(), handler=MagicMock())

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_reclaim_user_teams_passes_current_orgs():
    from manager_server.routers.org_cleanup_routers import reclaim_user_teams

    handler = _handler("cluster_01")
    gateway = AsyncMock()
    with (
        patch(f"{_MOD}.gateway_request", gateway),
        patch(f"{_MOD}._user_org_groups", AsyncMock(return_value=["grp1"])),
    ):
        response = await reclaim_user_teams(
            "u2", user=object(), handler=handler, authorization="Bearer token"
        )

    assert response.data["keep_group_ids"] == ["grp1"]
    assert gateway.await_args_list[0].args[2] == "/api/v1/team-quota/users/u2/stop"
    assert gateway.await_args_list[0].args[3] == {"keep_group_ids": ["grp1"]}


@pytest.mark.asyncio
async def test_reclaim_user_teams_stops_all_when_user_deleted():
    from manager_server.routers.org_cleanup_routers import reclaim_user_teams

    handler = _handler("cluster_01")
    gateway = AsyncMock()
    with (
        patch(f"{_MOD}.gateway_request", gateway),
        patch(f"{_MOD}._user_org_groups", AsyncMock(return_value=[])),
    ):
        await reclaim_user_teams(
            "u2", user=object(), handler=handler, authorization="Bearer token"
        )

    assert gateway.await_args_list[0].args[3] == {"keep_group_ids": []}


@pytest.mark.asyncio
async def test_reclaim_user_teams_aborts_when_identity_unavailable():
    from fastapi import HTTPException

    from manager_server.routers.org_cleanup_routers import reclaim_user_teams

    gateway = AsyncMock()
    with (
        patch(f"{_MOD}.gateway_request", gateway),
        patch(f"{_MOD}._user_org_groups", AsyncMock(return_value=None)),
    ):
        with pytest.raises(HTTPException) as exc:
            await reclaim_user_teams(
                "u2", user=object(), handler=_handler("cluster_01"), authorization="Bearer"
            )

    assert exc.value.status_code == 503
    gateway.assert_not_awaited()
