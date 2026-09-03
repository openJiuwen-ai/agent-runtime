"""User Console organization-context authorization tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from manager_server.core.user_console import GroupAccessDeniedError, UserConsoleService
from manager_server.core.instance_access import InstanceGrantService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_member_group_context_is_allowed() -> None:
    handler = AsyncMock()
    service = UserConsoleService(handler)
    is_admitted = AsyncMock(return_value=False)

    with patch.object(InstanceGrantService, "is_admitted", is_admitted):
        result = await service.list_visible_agents(
            "user-1",
            "group-a",
            ["group-a"],
            jiuwenclaw_id="instance-1",
        )

    assert result == []
    is_admitted.assert_awaited_once_with("instance-1", "user-1", {"group-a"})


@pytest.mark.asyncio
async def test_non_member_group_context_is_rejected_before_database_access() -> None:
    handler = AsyncMock()
    service = UserConsoleService(handler)

    with pytest.raises(GroupAccessDeniedError, match="group access denied"):
        await service.list_visible_agents(
            "user-1",
            "group-b",
            ["group-a"],
            jiuwenclaw_id="instance-1",
        )

    handler.list_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_org_context_is_allowed_only_without_group_claims() -> None:
    handler = AsyncMock()
    service = UserConsoleService(handler)
    is_admitted = AsyncMock(return_value=False)

    with patch.object(InstanceGrantService, "is_admitted", is_admitted):
        result = await service.list_visible_agents(
            "user-1",
            "__none__",
            [],
            jiuwenclaw_id="instance-1",
        )
    assert result == []

    with pytest.raises(GroupAccessDeniedError, match="group access denied"):
        await service.list_visible_agents(
            "user-1",
            "__none__",
            ["group-a"],
            jiuwenclaw_id="instance-1",
        )


@pytest.mark.asyncio
async def test_admin_can_use_any_group_context() -> None:
    handler = AsyncMock()
    handler.list_records.return_value = []
    service = UserConsoleService(handler)
    is_admitted = AsyncMock(return_value=False)

    with patch.object(InstanceGrantService, "is_admitted", is_admitted):
        result = await service.list_visible_agents(
            "admin",
            "group-b",
            [],
            jiuwenclaw_id="instance-1",
            is_admin=True,
        )

    assert result == []
    is_admitted.assert_not_awaited()
