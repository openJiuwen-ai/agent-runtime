# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""gateway online 全量同步触发条件。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from manager_server.core.instance.instance_service import (
    maybe_full_sync_gateway_on_online,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_skip_when_already_online():
    handler = AsyncMock()
    with patch(
        "manager_server.core.instance.instance_data_lifecycle.sync_data_to_gateway_on_register",
        new_callable=AsyncMock,
    ) as sync_mock:
        await maybe_full_sync_gateway_on_online(
            handler, "jid-1", previous_gateway_status="online"
        )
    sync_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_when_pending_to_online():
    handler = AsyncMock()
    with (
        patch(
            "manager_server.manager_config_push.endpoint.require_gateway_endpoint",
            new_callable=AsyncMock,
            return_value="http://gw:8080",
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.sync_data_to_gateway_on_register",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as sync_mock,
    ):
        await maybe_full_sync_gateway_on_online(
            handler, "jid-1", previous_gateway_status="pending"
        )
    sync_mock.assert_awaited_once_with(handler, "jid-1")


@pytest.mark.asyncio
async def test_sync_when_offline_to_online():
    handler = AsyncMock()
    with (
        patch(
            "manager_server.manager_config_push.endpoint.require_gateway_endpoint",
            new_callable=AsyncMock,
            return_value="http://gw:8080",
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.sync_data_to_gateway_on_register",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as sync_mock,
    ):
        await maybe_full_sync_gateway_on_online(
            handler, "jid-1", previous_gateway_status="offline"
        )
    sync_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_skip_when_no_gateway_host():
    handler = AsyncMock()
    with (
        patch(
            "manager_server.manager_config_push.endpoint.require_gateway_endpoint",
            new_callable=AsyncMock,
            side_effect=ValueError("no gateway_host"),
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.sync_data_to_gateway_on_register",
            new_callable=AsyncMock,
        ) as sync_mock,
    ):
        await maybe_full_sync_gateway_on_online(
            handler, "jid-1", previous_gateway_status="offline"
        )
    sync_mock.assert_not_awaited()
