# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""工作区配额策略：选路与先推 Gateway 再写 Manager。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_GW = "manager_server.core.quota.workspace_quota_policy.gateway_request"


def test_select_effective_policy_uses_lowest_priority():
    from manager_server.core.quota.workspace_quota_policy import select_effective_policy

    rows = [
        {
            "policy_id": "wide",
            "priority": 100,
            "enabled": True,
            "match_expr": [],
        },
        {
            "policy_id": "org",
            "priority": 50,
            "enabled": True,
            "match_expr": "group_id == 'g_sales'",
        },
        {
            "policy_id": "person",
            "priority": 10,
            "enabled": True,
            "match_expr": (
                "user_id == 'u_123' and group_id == 'g_sales' and bot_id == 'bot_writer'"
            ),
        },
        {
            "policy_id": "off",
            "priority": 1,
            "enabled": False,
            "match_expr": (
                "user_id == 'u_123' and group_id == 'g_sales' and bot_id == 'bot_writer'"
            ),
        },
    ]
    chosen = select_effective_policy(
        rows,
        user_id="u_123",
        group_id="g_sales",
        bot_id="bot_writer",
    )
    assert chosen is not None
    assert chosen["policy_id"] == "person"


@pytest.mark.asyncio
async def test_create_pushes_gateway_before_manager_write():
    from manager_server.core.quota.workspace_quota_policy import WorkspaceQuotaPolicyService

    handler = AsyncMock()
    handler.list_records = AsyncMock(return_value=[])
    order: list[str] = []

    async def _create(_table, row):
        order.append("create")
        return row

    handler.create = AsyncMock(side_effect=_create)

    async def _gw(*_args, **_kwargs):
        order.append("gateway")
        return {"success_flag": True}

    with patch(_GW, new_callable=AsyncMock, side_effect=_gw) as gw_mock:
        result = await WorkspaceQuotaPolicyService(handler).create(
            cluster_id="cluster_01",
            policy_name="个人额度",
            policy_desc="测试描述",
            match_expr="user_id == 'u_123'",
            priority=10,
            limit_bytes=21474836480,
        )

    assert order == ["gateway", "create"]
    assert result["cluster_id"] == "cluster_01"
    assert result["policy_name"] == "个人额度"
    assert result["policy_desc"] == "测试描述"
    assert result["priority"] == 10
    assert result["soft_percent"] == 80
    assert result["hard_percent"] == 100
    assert result["limit_bytes"] == 21474836480
    assert result["source"] == "manual"
    assert result["locked"] is False
    args = gw_mock.await_args.args
    assert args[0] == "cluster_01"
    assert args[1] == "PUT"
    assert args[2] == "/api/v1/workspace-quota/policies"
    assert "cluster_id" not in args[3]
    assert args[3]["policy_id"] == result["policy_id"]
    assert args[3]["policy_name"] == "个人额度"
    assert args[3]["source"] == "manual"
    assert args[3]["policy_desc"] == "测试描述"


@pytest.mark.asyncio
async def test_create_allows_zero_and_unlimited_limit():
    from manager_server.core.quota.workspace_quota_policy import WorkspaceQuotaPolicyService

    handler = AsyncMock()
    handler.list_records = AsyncMock(return_value=[])
    created: list[dict] = []

    async def _create(_table, row):
        created.append(row)
        return row

    handler.create = AsyncMock(side_effect=_create)

    with patch(_GW, new_callable=AsyncMock, return_value={"success_flag": True}):
        zero = await WorkspaceQuotaPolicyService(handler).create(
            cluster_id="cluster_01",
            policy_name="零配额",
            match_expr=[],
            priority=20,
            limit_bytes=0,
        )
        unlimited = await WorkspaceQuotaPolicyService(handler).create(
            cluster_id="cluster_01",
            policy_name="无限制",
            match_expr="user_id == 'u1'",
            priority=10,
            limit_bytes=-1,
        )

    assert zero["limit_bytes"] == 0
    assert unlimited["limit_bytes"] == -1


@pytest.mark.asyncio
async def test_create_rejects_invalid_negative_limit():
    from manager_server.core.quota.workspace_quota_policy import WorkspaceQuotaPolicyService

    handler = AsyncMock()
    handler.list_records = AsyncMock(return_value=[])
    handler.create = AsyncMock()

    with pytest.raises(ValueError, match="limit_bytes"):
        await WorkspaceQuotaPolicyService(handler).create(
            cluster_id="cluster_01",
            policy_name="非法",
            match_expr=[],
            priority=100,
            limit_bytes=-2,
        )
    handler.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_does_not_write_manager_when_gateway_fails():
    from manager_server.core.quota.workspace_quota_policy import WorkspaceQuotaPolicyService

    handler = AsyncMock()
    handler.list_records = AsyncMock(return_value=[])
    handler.create = AsyncMock()

    with patch(_GW, new_callable=AsyncMock, side_effect=RuntimeError("down")):
        with pytest.raises(ValueError, match="failed to sync"):
            await WorkspaceQuotaPolicyService(handler).create(
                cluster_id="cluster_01",
                policy_name="平台默认",
                match_expr=[],
                priority=100,
                limit_bytes=1,
            )
    handler.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_rejects_duplicate_priority():
    from manager_server.core.quota.workspace_quota_policy import WorkspaceQuotaPolicyService

    handler = AsyncMock()
    handler.list_records = AsyncMock(
        return_value=[
            {
                "policy_id": "other",
                "priority": 10,
                "match_expr": "group_id == 'g_other'",
                "enabled": True,
            }
        ]
    )
    handler.create = AsyncMock()

    with patch(_GW, new_callable=AsyncMock) as gw_mock:
        with pytest.raises(ValueError, match="priority"):
            await WorkspaceQuotaPolicyService(handler).create(
                cluster_id="cluster_01",
                policy_name="个人额度",
                match_expr="user_id == 'u_123'",
                priority=10,
                limit_bytes=1,
            )
    gw_mock.assert_not_awaited()
    handler.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_pushes_gateway_before_manager_delete():
    from manager_server.core.quota.workspace_quota_policy import WorkspaceQuotaPolicyService

    handler = AsyncMock()
    handler.get = AsyncMock(
        return_value={
            "policy_id": "qp_01",
            "cluster_id": "cluster_01",
            "match_expr": "user_id == 'u_123'",
        }
    )
    order: list[str] = []

    async def _delete(*_args, **_kwargs):
        order.append("delete")
        return True

    handler.delete = AsyncMock(side_effect=_delete)

    async def _gw(*_args, **_kwargs):
        order.append("gateway")
        return {"success_flag": True}

    with patch(_GW, new_callable=AsyncMock, side_effect=_gw) as gw_mock:
        await WorkspaceQuotaPolicyService(handler).delete("qp_01")

    assert order == ["gateway", "delete"]
    args = gw_mock.await_args.args
    assert args[1] == "DELETE"
    assert args[2] == "/api/v1/workspace-quota/policies/qp_01"


@pytest.mark.asyncio
async def test_delete_allows_full_match():
    from manager_server.core.quota.workspace_quota_policy import WorkspaceQuotaPolicyService

    handler = AsyncMock()
    handler.get = AsyncMock(
        return_value={
            "policy_id": "qp_default",
            "cluster_id": "cluster_01",
            "match_expr": [],
        }
    )
    order: list[str] = []

    async def _delete(*_args, **_kwargs):
        order.append("delete")
        return True

    handler.delete = AsyncMock(side_effect=_delete)

    async def _gw(*_args, **_kwargs):
        order.append("gateway")
        return {"success_flag": True}

    with patch(_GW, new_callable=AsyncMock, side_effect=_gw) as gw_mock:
        await WorkspaceQuotaPolicyService(handler).delete("qp_default")

    assert order == ["gateway", "delete"]
    args = gw_mock.await_args.args
    assert args[1] == "DELETE"
    assert args[2] == "/api/v1/workspace-quota/policies/qp_default"


def _quota_row(
    policy_id: str,
    *,
    priority: int,
    match_expr: object,
    limit_bytes: int,
    enabled: bool = True,
    source: str = "manual",
    source_order_num: str | None = None,
    policy_name: str | None = None,
    policy_desc: str | None = None,
) -> dict:
    return {
        "policy_id": policy_id,
        "cluster_id": "cluster_01",
        "policy_name": policy_name if policy_name is not None else policy_id,
        "policy_desc": policy_desc,
        "match_expr": match_expr,
        "priority": priority,
        "limit_bytes": limit_bytes,
        "soft_percent": 80,
        "hard_percent": 100,
        "source": source,
        "source_order_num": source_order_num,
        "enabled": enabled,
        "created_at": None,
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_list_policies_filters_sorts_and_pages_on_server():
    from manager_server.core.quota.workspace_quota_policy import WorkspaceQuotaPolicyService
    from manager_server.schemas.quota_schemas import WorkspaceQuotaPolicyListQuery

    rows = [
        _quota_row("wide", priority=100, match_expr=[], limit_bytes=1, enabled=False, policy_name="平台默认"),
        _quota_row(
            "org",
            priority=50,
            match_expr="group_id == 'g_sales'",
            limit_bytes=5 * 1024**3,
            source="approval",
            source_order_num="ORD-9",
            policy_name="销售组组织额度",
            policy_desc="组织级",
        ),
        _quota_row(
            "person",
            priority=10,
            match_expr="user_id == 'u_123'",
            limit_bytes=20 * 1024**3,
            policy_name="个人额度",
        ),
    ]
    handler = AsyncMock()

    async def _list_records(_table, filters, **_kwargs):
        matched = rows
        if filters and "enabled" in filters:
            matched = [row for row in matched if bool(row["enabled"]) is bool(filters["enabled"])]
        policy_id = (filters or {}).get("policy_id")
        if policy_id:
            matched = [row for row in matched if row["policy_id"] == policy_id]
        return matched

    handler.list_records = AsyncMock(side_effect=_list_records)
    svc = WorkspaceQuotaPolicyService(handler)

    default = await svc.list_policies(cluster_id="cluster_01", query=WorkspaceQuotaPolicyListQuery())
    assert [item["policy_id"] for item in default["items"]] == ["person", "org", "wide"]
    assert default["total"] == 3
    assert default["page"] == 1
    assert default["page_size"] == 20
    filters = handler.list_records.await_args.args[1]
    assert filters["cluster_id"] == "cluster_01"
    assert "enabled" not in filters

    enabled_only = await svc.list_policies(
        cluster_id="cluster_01",
        query=WorkspaceQuotaPolicyListQuery(enabled=True),
    )
    assert [item["policy_id"] for item in enabled_only["items"]] == ["person", "org"]

    searched = await svc.list_policies(
        cluster_id="cluster_01",
        query=WorkspaceQuotaPolicyListQuery(search="销售组"),
    )
    assert [item["policy_id"] for item in searched["items"]] == ["org"]
    assert searched["total"] == 1

    by_name = await svc.list_policies(
        cluster_id="cluster_01",
        query=WorkspaceQuotaPolicyListQuery(sort_by="policy_name", sort_order="asc"),
    )
    assert [item["policy_id"] for item in by_name["items"]] == ["person", "wide", "org"]

    by_limit = await svc.list_policies(
        cluster_id="cluster_01",
        query=WorkspaceQuotaPolicyListQuery(sort_by="limit_bytes", sort_order="desc", page_size=2),
    )
    assert [item["policy_id"] for item in by_limit["items"]] == ["person", "org"]
    assert by_limit["total"] == 3

    page_two = await svc.list_policies(
        cluster_id="cluster_01",
        query=WorkspaceQuotaPolicyListQuery(
            sort_by="limit_bytes",
            sort_order="desc",
            page=2,
            page_size=2,
        ),
    )
    assert [item["policy_id"] for item in page_two["items"]] == ["wide"]
    assert page_two["page"] == 2

    fallback = await svc.list_policies(
        cluster_id="cluster_01",
        query=WorkspaceQuotaPolicyListQuery(sort_by="not_a_field", sort_order="desc"),
    )
    assert [item["policy_id"] for item in fallback["items"]] == ["person", "org", "wide"]


@pytest.mark.asyncio
async def test_create_rejects_negative_priority_for_admin():
    from manager_server.core.quota.workspace_quota_policy import (
        WorkspaceQuotaPolicyService,
    )

    handler = AsyncMock()
    handler.list_records = AsyncMock(return_value=[])
    handler.create = AsyncMock()
    with pytest.raises(ValueError, match="priority < 0"):
        await WorkspaceQuotaPolicyService(handler).create(
            cluster_id="cluster_01",
            policy_name="非法负优先级",
            match_expr="user_id == 'u1'",
            priority=-1,
            limit_bytes=1024,
        )
    handler.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_rejects_admin_setting_source_order_num():
    from manager_server.core.quota.workspace_quota_policy import (
        WorkspaceQuotaPolicyService,
    )

    handler = AsyncMock()
    handler.list_records = AsyncMock(return_value=[])
    handler.create = AsyncMock()
    with pytest.raises(ValueError, match="source_order_num"):
        await WorkspaceQuotaPolicyService(handler).create(
            cluster_id="cluster_01",
            policy_name="伪造审批",
            match_expr="user_id == 'u1'",
            priority=10,
            limit_bytes=1024,
            source_order_num="apr_fake",
        )
    handler.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_assert_unique_allows_duplicate_negative_priority():
    from manager_server.core.quota.workspace_quota_policy import (
        APPROVAL_POLICY_PRIORITY,
        WorkspaceQuotaPolicyService,
    )

    handler = AsyncMock()
    handler.list_records = AsyncMock(
        return_value=[
            {
                "policy_id": "apr_a",
                "priority": APPROVAL_POLICY_PRIORITY,
                "match_expr": "user_id == 'u_a' and group_id == 'g' and bot_id == 'b'",
                "enabled": True,
                "source": "approval",
                "source_order_num": "apr_01",
            }
        ]
    )
    svc = WorkspaceQuotaPolicyService(handler)
    # 另一条审批策略同为 -1、不同 match_expr，应允许。
    # 管理面 create 禁止 priority<0，故用 getattr 覆盖白盒校验（避免 G.CLS.11）。
    assert_unique = getattr(svc, "_assert_unique")
    await assert_unique(
        cluster_id="cluster_01",
        match_expr="user_id == 'u_b' and group_id == 'g' and bot_id == 'b'",
        priority=APPROVAL_POLICY_PRIORITY,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_update_locks_approval_sourced_policy():
    from manager_server.core.quota.workspace_quota_policy import (
        WorkspaceQuotaPolicyService,
    )

    row = {
        "policy_id": "qp_apr",
        "cluster_id": "cluster_01",
        "policy_name": "quota-expand-apr_01",
        "policy_desc": "Created by workspace quota approval",
        "match_expr": "user_id == 'u1' and group_id == 'g' and bot_id == 'b'",
        "priority": -1,
        "limit_bytes": 20_000,
        "soft_percent": 80,
        "hard_percent": 100,
        "source": "approval",
        "source_order_num": "apr_01",
        "enabled": True,
        "created_at": None,
        "created_by": "u_admin",
        "updated_at": None,
        "updated_by": "u_admin",
    }
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=row)
    handler.list_records = AsyncMock(return_value=[row])
    handler.update = AsyncMock(return_value={**row, "enabled": False})

    svc = WorkspaceQuotaPolicyService(handler)
    with pytest.raises(ValueError, match="locked"):
        await svc.update("qp_apr", {"limit_bytes": 30_000}, cluster_id="cluster_01")

    with patch(_GW, new_callable=AsyncMock, return_value={"success_flag": True}):
        updated = await svc.update("qp_apr", {"enabled": False}, cluster_id="cluster_01")
    assert updated is not None
    assert updated["locked"] is True
    assert updated["enabled"] is False
