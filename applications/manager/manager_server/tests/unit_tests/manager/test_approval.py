# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""工作区配额扩容审批：提交唯一性、状态机与批准后 sync。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from manager_server.core.approval.service import ApprovalService, WORKSPACE_QUOTA_EXPAND
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.auth_guards import get_current_user

pytestmark = pytest.mark.unit

_GW = "manager_server.core.approval.service.gateway_request"
_LOOKUP = "manager_server.core.approval.service.ApprovalService._lookup_display_names"
_LIST_ACT = (
    "manager_server.core.authz.service.AuthzService.list_users_with_permission"
)
_NOW = datetime(2026, 3, 17, 10, 0, 0, tzinfo=UTC)


def _apply_data(**overrides):
    data = {
        "workspace_key": "workspace_abc",
        "current_limit_bytes": 5_000,
        "requested_limit_bytes": 20_000,
        "used_bytes": 4_000,
        "usage_percent": 80.0,
        "source_policy_id": "qp_default",
    }
    data.update(overrides)
    return data


def _order_row(**overrides):
    row = {
        "order_num": "apr_01",
        "business_type": WORKSPACE_QUOTA_EXPAND,
        "title": "工作区扩容",
        "status": "pending",
        "applicant_id": "u_app",
        "approver_id": None,
        "group_id": "g_sales",
        "bot_id": "bot_writer",
        "cluster_id": "cluster_01",
        "reason": "项目交付产出较多",
        "apply_data": _apply_data(),
        "result_data": None,
        "finished_at": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_submit_rejects_non_increasing_quota():
    handler = AsyncMock()
    svc = ApprovalService(handler)
    with pytest.raises(ValueError, match="requested_limit_bytes"):
        await svc.submit_workspace_quota_expand(
            applicant_id="u_app",
            group_id="g_sales",
            bot_id="bot_writer",
            cluster_id="cluster_01",
            workspace_key="wk",
            current_limit_bytes=5000,
            requested_limit_bytes=5000,
            used_bytes=1000,
            source_policy_id="qp_01",
            reason="持平",
            approver_id="u_admin",
        )


@pytest.mark.asyncio
async def test_applicant_cannot_approve_own_request():
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=_order_row())
    svc = ApprovalService(handler)
    with pytest.raises(PermissionError, match="applicant cannot"):
        await svc.act(order_num="apr_01", operator_id="u_app", action="approve", comment=None)


@pytest.mark.asyncio
async def test_act_rejects_non_designated_approver():
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=_order_row(approver_id="u_admin"))
    svc = ApprovalService(handler)
    with pytest.raises(PermissionError, match="designated approver"):
        await svc.act(order_num="apr_01", operator_id="u_other", action="approve", comment=None)


@pytest.mark.asyncio
async def test_list_mine_shows_designated_approver_only():
    handler = AsyncMock()
    handler.list_records = AsyncMock(return_value=[_order_row(approver_id="u_admin")])
    svc = ApprovalService(handler)
    with (
        patch(_LIST_ACT, new_callable=AsyncMock, return_value=["u_admin", "u_mgr"]),
        patch(
            _LOOKUP,
            new_callable=AsyncMock,
            return_value={"u_admin": "平台管理员", "u_mgr": "经理"},
        ),
    ):
        items = await svc.list_mine(applicant_id="u_app")
    assert items[0]["approver_id"] == "u_admin"
    assert items[0]["current_approvers"] == [
        {"user_id": "u_admin", "display_name": "平台管理员"}
    ]


@pytest.mark.asyncio
async def test_list_orders_todo_only_designated_approver():
    rows = [
        _order_row(order_num="apr_mine", applicant_id="u_admin", approver_id="u_admin"),
        _order_row(order_num="apr_other", applicant_id="u_app", approver_id="u_other"),
        _order_row(order_num="apr_open", applicant_id="u_app", approver_id=None),
        _order_row(order_num="apr_for_me", applicant_id="u_app", approver_id="u_admin"),
        _order_row(
            order_num="apr_done",
            applicant_id="u_app",
            approver_id="u_admin",
            status="approved",
        ),
    ]

    async def _list_records(_table, filters, limit, offset):
        status = filters.get("status")
        if status:
            return [row for row in rows if row["status"] == status]
        return list(rows)

    handler = AsyncMock()
    handler.list_records = AsyncMock(side_effect=_list_records)
    svc = ApprovalService(handler)
    items = await svc.list_orders(
        operator_id="u_admin",
        business_type=None,
        view="todo",
        status=None,
        group_id=None,
    )
    assert [item["order_num"] for item in items] == ["apr_for_me", "apr_done"]
    assert items[0]["reason"] == "项目交付产出较多"

    pending_only = await svc.list_orders(
        operator_id="u_admin",
        business_type=None,
        view="todo",
        status="pending",
        group_id=None,
    )
    assert [item["order_num"] for item in pending_only] == ["apr_for_me"]


@pytest.mark.asyncio
async def test_list_orders_todo_filters_by_search():
    handler = AsyncMock()
    handler.list_records = AsyncMock(
        return_value=[
            _order_row(
                order_num="apr_alpha",
                applicant_id="u_app",
                approver_id="u_admin",
                title="工作区扩容",
                reason="磁盘告急",
            ),
            _order_row(
                order_num="apr_beta",
                applicant_id="u_app",
                approver_id="u_admin",
                title="另一单",
                reason="项目交付",
            ),
        ]
    )
    svc = ApprovalService(handler)
    by_order = await svc.list_orders(
        operator_id="u_admin",
        business_type=None,
        view="todo",
        status=None,
        group_id=None,
        search="APR_BETA",
    )
    by_reason = await svc.list_orders(
        operator_id="u_admin",
        business_type=None,
        view="todo",
        status=None,
        group_id=None,
        search="磁盘",
    )
    assert [item["order_num"] for item in by_order] == ["apr_beta"]
    assert [item["order_num"] for item in by_reason] == ["apr_alpha"]



@pytest.mark.asyncio
async def test_list_mine_filters_by_search():
    handler = AsyncMock()
    handler.list_records = AsyncMock(
        return_value=[
            _order_row(order_num="apr_01", title="工作区扩容", reason="项目交付产出较多"),
            _order_row(
                order_num="apr_02",
                title="另一单",
                reason="磁盘告急",
                created_at=datetime(2026, 3, 18, 10, 0, 0, tzinfo=UTC),
            ),
        ]
    )
    svc = ApprovalService(handler)
    with (
        patch(_LIST_ACT, new_callable=AsyncMock, return_value=[]),
        patch(_LOOKUP, new_callable=AsyncMock, return_value={}),
    ):
        by_order = await svc.list_mine(applicant_id="u_app", search="APR_02")
        by_title = await svc.list_mine(applicant_id="u_app", search="工作区")
        by_reason = await svc.list_mine(applicant_id="u_app", search="磁盘")
        miss = await svc.list_mine(applicant_id="u_app", search="不存在的关键字")
    assert [item["order_num"] for item in by_order] == ["apr_02"]
    assert [item["order_num"] for item in by_title] == ["apr_01"]
    assert [item["order_num"] for item in by_reason] == ["apr_02"]
    assert miss == []


@pytest.mark.asyncio
async def test_cancel_only_by_applicant():
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=_order_row())
    handler.create = AsyncMock()
    handler.update = AsyncMock()
    svc = ApprovalService(handler)

    with pytest.raises(PermissionError, match="only applicant"):
        await svc.cancel(order_num="apr_01", operator_id="u_other")

    cancelled = await svc.cancel(order_num="apr_01", operator_id="u_app")
    assert cancelled == {"order_num": "apr_01", "status": "cancelled"}
    handler.update.assert_awaited()


@pytest.mark.asyncio
async def test_submit_reuses_cancelled_order_like_rejected():
    """撤回后再次提交应复用同一张单回到 pending，而不是新建。"""
    from unittest.mock import MagicMock

    cancelled_row = {
        "order_num": "apr_01",
        "status": "cancelled",
        "id": 1,
    }
    execute_result = MagicMock()
    execute_result.mappings.return_value.all.return_value = [cancelled_row]
    connection = AsyncMock()
    connection.execute = AsyncMock(return_value=execute_result)
    table = MagicMock()

    query_chain = MagicMock()
    query_chain.where.return_value = query_chain
    query_chain.order_by.return_value = query_chain
    query_chain.with_for_update.return_value = query_chain

    update_chain = MagicMock()
    update_chain.where.return_value = update_chain
    update_chain.values.return_value = update_chain

    with (
        patch("manager_server.core.approval.service.select", return_value=query_chain),
        patch("manager_server.core.approval.service.update", return_value=update_chain),
        patch("manager_server.core.approval.service.insert") as insert_mock,
    ):
        result = await ApprovalService._submit_workspace_quota_expand_in_transaction(
            connection,
            table,
            applicant_id="u_app",
            group_id="g_sales",
            bot_id="bot_writer",
            cluster_id="cluster_01",
            reason="撤回后再提",
            apply_data=_apply_data(),
            approver_id="u_admin",
        )

    assert result == {"order_num": "apr_01", "status": "pending"}
    insert_mock.assert_not_called()
    assert connection.execute.await_count == 2

@pytest.mark.asyncio
async def test_approve_records_sync_failure_and_allows_retry():
    handler = AsyncMock()
    row = _order_row()
    policy_stored = {
        "policy_id": "qp_01",
        "cluster_id": "cluster_01",
        "policy_name": "quota-expand-apr_01",
        "policy_desc": "Created by workspace quota approval",
        "match_expr": "user_id == 'u_app'",
        "priority": 10,
        "limit_bytes": 20_000,
        "soft_percent": 80,
        "hard_percent": 100,
        "source_order_num": "apr_01",
        "source": "approval",
        "enabled": True,
    }
    handler.get = AsyncMock(
        side_effect=[
            row,
            policy_stored,
            {
                **row,
                "status": "approved",
                "result_data": {
                    "policy_id": "qp_01",
                    "limit_bytes": 20_000,
                    "sync_status": "failed",
                    "sync_detail": "gw down",
                },
            },
            policy_stored,
        ]
    )
    handler.list_records = AsyncMock(return_value=[])
    handler.create = AsyncMock()
    handler.update = AsyncMock()
    svc = ApprovalService(handler)

    with patch(_GW, new_callable=AsyncMock, side_effect=RuntimeError("gw down")) as gw_fail:
        approved = await svc.act(
            order_num="apr_01",
            operator_id="u_admin",
            action="approve",
            comment="同意",
        )
    assert approved["status"] == "approved"
    assert approved["policy_id"] == "qp_01"
    fail_body = gw_fail.await_args.args[3]
    assert fail_body["policy_name"] == "quota-expand-apr_01"
    assert "cluster_id" not in fail_body
    update_payload = handler.update.await_args_list[-1].args[2]
    assert update_payload["result_data"]["sync_status"] == "failed"

    # sync retry path
    failed_row = {
        **row,
        "status": "approved",
        "approver_id": "u_admin",
        "result_data": update_payload["result_data"],
    }
    handler.get = AsyncMock(
        side_effect=[
            failed_row,
            policy_stored,
        ]
    )
    handler.list_records = AsyncMock(
        return_value=[
            {
                "policy_id": "qp_01",
                "cluster_id": "cluster_01",
                "policy_name": "quota-expand-apr_01",
                "policy_desc": "Created by workspace quota approval",
                "match_expr": (
                    "user_id == 'u_app' and group_id == 'g_sales' and bot_id == 'bot_writer'"
                ),
                "priority": 10,
                "limit_bytes": 5_000,
                "soft_percent": 80,
                "hard_percent": 100,
                "source": "approval",
                "source_order_num": "apr_01",
                "enabled": True,
            }
        ]
    )
    with patch(_GW, new_callable=AsyncMock, return_value={"success_flag": True}) as gw:
        retried = await svc.act(
            order_num="apr_01",
            operator_id="u_admin",
            action="approve",
            comment="重试",
        )
    assert retried["status"] == "approved"
    assert gw.await_count == 1
    retry_body = gw.await_args.args[3]
    assert retry_body["policy_name"] == "quota-expand-apr_01"
    assert retry_body["policy_id"] == "qp_01"
    retry_payload = handler.update.await_args_list[-1].args[2]
    assert retry_payload["result_data"]["sync_status"] == "success"


@pytest.mark.asyncio
async def test_list_mine_includes_current_approvers_when_pending():
    handler = AsyncMock()
    handler.list_records = AsyncMock(return_value=[_order_row()])
    svc = ApprovalService(handler)
    with (
        patch(_LIST_ACT, new_callable=AsyncMock, return_value=["u_admin", "u_app"]),
        patch(_LOOKUP, new_callable=AsyncMock, return_value={"u_admin": "平台管理员"}),
    ):
        items = await svc.list_mine(applicant_id="u_app")
    assert len(items) == 1
    assert items[0]["business_type"] == WORKSPACE_QUOTA_EXPAND
    assert items[0]["title"] == "工作区扩容"
    assert items[0]["apply_data"]["requested_limit_bytes"] == 20_000
    assert items[0]["latest_reject_comment"] == ""
    assert items[0]["current_approvers"] == [
        {"user_id": "u_admin", "display_name": "平台管理员"}
    ]


@pytest.mark.asyncio
async def test_list_mine_includes_latest_reject_comment():
    handler = AsyncMock()

    async def _list_records(table, filters, limit=0, offset=0):
        if table == "approval_order":
            return [_order_row(status="rejected", approver_id="u_admin")]
        if table == "approval_record":
            return [
                {
                    "action": "reject",
                    "comment": "配额过大",
                    "created_at": datetime(2026, 3, 17, 11, 0, 0, tzinfo=UTC),
                },
                {
                    "action": "reject",
                    "comment": "请降低目标配额",
                    "created_at": datetime(2026, 3, 17, 12, 0, 0, tzinfo=UTC),
                },
            ]
        return []

    handler.list_records = AsyncMock(side_effect=_list_records)
    svc = ApprovalService(handler)
    with (
        patch(_LIST_ACT, new_callable=AsyncMock, return_value=["u_admin"]),
        patch(_LOOKUP, new_callable=AsyncMock, return_value={"u_admin": "平台管理员"}),
    ):
        items = await svc.list_mine(applicant_id="u_app")
    assert items[0]["status"] == "rejected"
    assert items[0]["latest_reject_comment"] == "请降低目标配额"
    assert items[0]["current_approvers"] == []


@pytest.mark.asyncio
async def test_http_submit_and_mine_routes(monkeypatch):
    from manager_server.infrastructure.config import settings

    monkeypatch.setattr(settings, "workspace_quota_enabled", True)
    from manager_server.routers.user_console_routers import user_console_router

    app = FastAPI()
    app.include_router(user_console_router, prefix="/api/v1/user-console")
    handler = AsyncMock()

    async def _applicant():
        return SimpleNamespace(user_id="u_app", is_admin=False, groups=[], display_name="App")

    app.dependency_overrides[get_db_handler] = lambda: handler
    app.dependency_overrides[get_current_user] = _applicant

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch(
                "manager_server.routers.user_console_routers.UserConsoleService.user_can_access_context",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(
                ApprovalService,
                "submit_from_user_console",
                new_callable=AsyncMock,
                return_value={"order_num": "apr_01", "status": "pending"},
            ),
        ):
            submit = await client.post(
                "/api/v1/user-console/workspace/expand-requests",
                json={
                    "jiuwenclaw_id": "cluster_01",
                    "group_id": "g_sales",
                    "bot_id": "bot_writer",
                    "requested_limit_bytes": 20000,
                    "reason": "需要扩容",
                    "used_bytes": 4000,
                    "approver_id": "u_admin",
                },
            )
        assert submit.status_code == 200, submit.text
        assert submit.json()["data"]["order_num"] == "apr_01"

        with patch.object(
            ApprovalService,
            "list_mine",
            new_callable=AsyncMock,
            return_value=[
                {
                    "order_num": "apr_01",
                    "business_type": WORKSPACE_QUOTA_EXPAND,
                    "title": "工作区扩容",
                    "status": "pending",
                    "reason": "需要扩容",
                    "apply_data": {"requested_limit_bytes": 20000},
                    "current_approvers": [],
                    "created_at": "2026-03-17T10:00:00Z",
                }
            ],
        ):
            mine = await client.get("/api/v1/user-console/approvals/mine")
        assert mine.status_code == 200, mine.text
        assert mine.json()["data"]["items"][0]["order_num"] == "apr_01"
