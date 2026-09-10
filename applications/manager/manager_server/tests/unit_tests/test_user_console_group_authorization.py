"""User Console accessible agent-context enumeration tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from manager_server.core.user_console import UserConsoleService
from manager_server.core.instance_access import InstanceGrantService
from manager_server.core.user_console import services as user_console_services

pytestmark = pytest.mark.unit


def _resource(
    *,
    resource_id: str,
    jiuwenclaw_id: str = "gw-1",
    resource_name: str = "",
    match_expr: object = None,
    enabled: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        jiuwenclaw_id=jiuwenclaw_id,
        resource_id=resource_id,
        resource_name=resource_name or resource_id,
        match_expr=match_expr if match_expr is not None else [],
        enabled=enabled,
        expires_at=None,
    )


@pytest.mark.asyncio
async def test_enumerates_matching_group_and_user_contexts() -> None:
    handler = AsyncMock()
    handler.list_records = AsyncMock(
        return_value=[
            _resource(resource_id="bot-sales", resource_name="Sales", match_expr="group_id in ('g-sales')"),
            _resource(resource_id="bot-alice", resource_name="Alice", match_expr="user_id == 'alice'"),
            _resource(resource_id="bot-all", resource_name="All", match_expr=[]),
        ]
    )
    service = UserConsoleService(handler)

    with (
        patch.object(
            user_console_services,
            "_load_orgs",
            AsyncMock(
                return_value=[
                    {"group_id": "g-sales", "group_name": "销售组"},
                    {"group_id": "g-other", "group_name": "其他组"},
                ]
            ),
        ),
        patch.object(
            InstanceGrantService,
            "list_instances_for",
            AsyncMock(side_effect=[{"alice": ["gw-1"]}, {"g-sales": ["gw-1"], "g-other": []}]),
        ),
        patch.object(InstanceGrantService, "is_admitted", AsyncMock(return_value=True)),
    ):
        result = await service.list_accessible_contexts(
            "alice",
            ["g-sales", "g-other"],
            authorization="Bearer t",
        )

    keys = {(x["bot_id"], x["group_id"], x["user_id"]) for x in result}
    assert ("bot-sales", "g-sales", "alice") in keys
    assert ("bot-sales", "g-other", "alice") not in keys
    assert ("bot-alice", "g-sales", "alice") in keys
    assert ("bot-alice", "g-other", "alice") in keys
    assert ("bot-all", "g-sales", "alice") in keys
    assert ("bot-all", "g-other", "alice") in keys
    assert all(x["jiuwenclaw_id"] == "gw-1" for x in result)
    sales = next(x for x in result if x["bot_id"] == "bot-sales")
    assert sales["agent_name"] == "Sales"
    assert sales["group_name"] == "销售组"


@pytest.mark.asyncio
async def test_no_org_uses_none_group_context() -> None:
    handler = AsyncMock()
    handler.list_records = AsyncMock(
        return_value=[_resource(resource_id="bot-1", match_expr=[])]
    )
    service = UserConsoleService(handler)

    with (
        patch.object(user_console_services, "_load_orgs", AsyncMock(return_value=[])),
        patch.object(
            InstanceGrantService,
            "list_instances_for",
            AsyncMock(return_value={"alice": ["gw-1"]}),
        ),
        patch.object(InstanceGrantService, "is_admitted", AsyncMock(return_value=True)),
    ):
        result = await service.list_accessible_contexts("alice", [])

    assert result == [
        {
            "bot_id": "bot-1",
            "group_id": "__none__",
            "user_id": "alice",
            "jiuwenclaw_id": "gw-1",
            "agent_name": "bot-1",
            "group_name": "无组织",
        }
    ]


@pytest.mark.asyncio
async def test_empty_when_not_admitted() -> None:
    handler = AsyncMock()
    service = UserConsoleService(handler)

    with (
        patch.object(
            user_console_services,
            "_load_orgs",
            AsyncMock(return_value=[{"group_id": "g-sales", "group_name": "销售组"}]),
        ),
        patch.object(
            InstanceGrantService,
            "list_instances_for",
            AsyncMock(return_value={"alice": ["gw-1"]}),
        ),
        patch.object(InstanceGrantService, "is_admitted", AsyncMock(return_value=False)),
    ):
        result = await service.list_accessible_contexts(
            "alice", ["g-sales"], authorization="Bearer t"
        )

    assert result == []
    handler.list_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_lists_all_instances_without_admission() -> None:
    handler = AsyncMock()

    async def list_records(table: str, *_args, **_kwargs):
        if table == "instance_info":
            return [SimpleNamespace(jiuwenclaw_id="gw-admin")]
        return [_resource(resource_id="bot-admin", jiuwenclaw_id="gw-admin", match_expr=[])]

    handler.list_records = AsyncMock(side_effect=list_records)
    service = UserConsoleService(handler)
    is_admitted = AsyncMock(return_value=False)

    with (
        patch.object(
            user_console_services,
            "_load_orgs",
            AsyncMock(return_value=[{"group_id": "g-any", "group_name": "任意组"}]),
        ),
        patch.object(InstanceGrantService, "is_admitted", is_admitted),
    ):
        result = await service.list_accessible_contexts(
            "admin", ["g-any"], is_admin=True, authorization="Bearer t"
        )

    assert [(x["bot_id"], x["group_id"], x["group_name"]) for x in result] == [
        ("bot-admin", "g-any", "任意组")
    ]
    is_admitted.assert_not_awaited()
