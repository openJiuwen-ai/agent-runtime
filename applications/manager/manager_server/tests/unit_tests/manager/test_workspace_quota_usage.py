# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""工作区用量：Gateway 同步后写入 Manager 再查询。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_GW = "manager_server.core.quota.workspace_quota_usage.gateway_request"


def _now() -> datetime:
    return datetime(2026, 3, 17, 10, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_list_usage_syncs_gateway_and_returns_manager_rows():
    from manager_server.core.quota.workspace_quota_usage import WorkspaceQuotaUsageService
    from manager_server.schemas.quota_schemas import WorkspaceQuotaUsageListQuery

    handler = MagicMock()
    handler.list_records = AsyncMock(
        side_effect=[
            [],
            [
                {
                    "cluster_id": "cluster_01",
                    "user_id": "u_123",
                    "group_id": "g_sales",
                    "bot_id": "bot_writer",
                    "used_bytes": 5368709120,
                    "limit_bytes": 21474836480,
                    "source_policy_id": "qp_01",
                    "status": "ok",
                    "reported_at": _now(),
                }
            ],
        ]
    )
    handler.get = AsyncMock(return_value=None)
    handler.create = AsyncMock(return_value={"id": 1})
    handler.update = AsyncMock()

    svc = WorkspaceQuotaUsageService(handler)
    gw = AsyncMock(
        return_value={
            "success_flag": True,
            "result": {
                "items": [
                    {
                        "user_id": "u_123",
                        "group_id": "g_sales",
                        "bot_id": "bot_writer",
                        "used_bytes": 5368709120,
                        "limit_bytes": 21474836480,
                        "source_policy_id": "qp_01",
                        "status": "ok",
                        "reported_at": "2026-03-17T10:05:00Z",
                    }
                ]
            },
        }
    )
    with patch(_GW, gw):
        data = await svc.list_usage(
            cluster_id="cluster_01",
            query=WorkspaceQuotaUsageListQuery(page=1, page_size=20),
            actor_id="admin",
        )

    assert data["total"] == 1
    assert data["items"][0]["user_id"] == "u_123"
    assert data["items"][0]["cluster_id"] == "cluster_01"
    assert data["summary"]["subject_count"] == 1
    assert data["summary"]["ok_count"] == 1
    assert gw.await_count == 1
    assert handler.create.await_count == 1


@pytest.mark.asyncio
async def test_list_usage_falls_back_to_local_when_gateway_fails():
    from manager_server.core.quota.workspace_quota_usage import WorkspaceQuotaUsageService
    from manager_server.schemas.quota_schemas import WorkspaceQuotaUsageListQuery

    handler = MagicMock()
    handler.list_records = AsyncMock(
        side_effect=[
            [],
            [
                {
                    "cluster_id": "cluster_01",
                    "user_id": "u_cached",
                    "group_id": "",
                    "bot_id": "bot_a",
                    "used_bytes": 100,
                    "limit_bytes": 1000,
                    "source_policy_id": "qp_x",
                    "status": "ok",
                    "reported_at": _now(),
                }
            ],
        ]
    )

    svc = WorkspaceQuotaUsageService(handler)
    with patch(_GW, AsyncMock(side_effect=ValueError("gateway down"))):
        data = await svc.list_usage(
            cluster_id="cluster_01",
            query=WorkspaceQuotaUsageListQuery(),
        )

    assert data["total"] == 1
    assert data["items"][0]["user_id"] == "u_cached"


@pytest.mark.asyncio
async def test_list_usage_synthesizes_status_when_gateway_omits_fields():
    from manager_server.core.quota.workspace_quota_usage import WorkspaceQuotaUsageService
    from manager_server.schemas.quota_schemas import WorkspaceQuotaUsageListQuery

    handler = MagicMock()
    policy_row = {
        "policy_id": "qp_01",
        "priority": 10,
        "enabled": True,
        "match_expr": [],
        "limit_bytes": 1000,
        "soft_percent": 80,
        "hard_percent": 100,
    }
    stored: dict = {}

    async def _create(_table, row):
        stored.update(row)
        return row

    async def _list(table, filters=None, limit=500, offset=0):
        if table == "workspace_quota_policy":
            return [policy_row] if offset == 0 else []
        if offset == 0 and stored:
            return [dict(stored)]
        return []

    handler.list_records = AsyncMock(side_effect=_list)
    handler.get = AsyncMock(return_value=None)
    handler.create = AsyncMock(side_effect=_create)
    handler.update = AsyncMock()

    svc = WorkspaceQuotaUsageService(handler)
    with patch(
        _GW,
        AsyncMock(
            return_value={
                "success_flag": True,
                "result": {
                    "items": [
                        {
                            "user_id": "u_1",
                            "group_id": "g_1",
                            "bot_id": "b_1",
                            "used_bytes": 900,
                            "reported_at": "2026-03-17T10:05:00Z",
                        }
                    ]
                },
            }
        ),
    ):
        data = await svc.list_usage(
            cluster_id="cluster_01",
            query=WorkspaceQuotaUsageListQuery(),
        )

    assert stored["status"] == "warn"
    assert stored["limit_bytes"] == 1000
    assert stored["source_policy_id"] == "qp_01"
    assert data["summary"]["warn_count"] == 1


@pytest.mark.asyncio
async def test_list_usage_search_matches_user_group_or_bot():
    from manager_server.core.quota.workspace_quota_usage import WorkspaceQuotaUsageService
    from manager_server.schemas.quota_schemas import WorkspaceQuotaUsageListQuery

    rows = [
        {
            "cluster_id": "cluster_01",
            "user_id": "alice",
            "group_id": "g_sales",
            "bot_id": "bot_a",
            "used_bytes": 100,
            "limit_bytes": 1000,
            "source_policy_id": "qp_1",
            "status": "ok",
            "reported_at": _now(),
        },
        {
            "cluster_id": "cluster_01",
            "user_id": "bob",
            "group_id": "g_eng",
            "bot_id": "bot_writer",
            "used_bytes": 200,
            "limit_bytes": 1000,
            "source_policy_id": "qp_1",
            "status": "ok",
            "reported_at": _now(),
        },
    ]
    handler = MagicMock()

    async def _list(table, filters=None, limit=500, offset=0):
        if table == "workspace_quota_policy":
            return [] if offset == 0 else []
        return list(rows) if offset == 0 else []

    handler.list_records = AsyncMock(side_effect=_list)
    svc = WorkspaceQuotaUsageService(handler)

    with patch(_GW, AsyncMock(side_effect=ValueError("skip sync"))):
        by_user = await svc.list_usage(
            cluster_id="cluster_01",
            query=WorkspaceQuotaUsageListQuery(search="ali"),
        )
        by_group = await svc.list_usage(
            cluster_id="cluster_01",
            query=WorkspaceQuotaUsageListQuery(search="eng"),
        )
        by_bot = await svc.list_usage(
            cluster_id="cluster_01",
            query=WorkspaceQuotaUsageListQuery(search="writer"),
        )

    assert [item["user_id"] for item in by_user["items"]] == ["alice"]
    assert [item["user_id"] for item in by_group["items"]] == ["bob"]
    assert [item["user_id"] for item in by_bot["items"]] == ["bob"]
    assert by_bot["summary"]["subject_count"] == 1
