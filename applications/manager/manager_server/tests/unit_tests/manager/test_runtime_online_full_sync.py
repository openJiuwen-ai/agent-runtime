# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""runtime online 全量 config_sync 触发条件。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from manager_server.core.instance.instance_service import (
    maybe_full_sync_runtime_on_online,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_skip_when_already_online():
    handler = AsyncMock()
    with patch(
        "manager_server.core.instance_resource.runtime_config_sync.sync_runtime_config",
        new_callable=AsyncMock,
    ) as sync_mock:
        await maybe_full_sync_runtime_on_online(
            handler, "jid-1", previous_runtime_status="online"
        )
    sync_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_when_pending_to_online():
    handler = AsyncMock()
    with (
        patch(
            "manager_server.infrastructure.config.settings.agent_runtime_endpoint",
            "http://runtime:8091",
        ),
        patch(
            "manager_server.core.instance_resource.runtime_config_sync.sync_runtime_config",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as sync_mock,
    ):
        await maybe_full_sync_runtime_on_online(
            handler, "jid-1", previous_runtime_status="pending"
        )
    sync_mock.assert_awaited_once_with(handler, "jid-1")


@pytest.mark.asyncio
async def test_sync_when_offline_to_online():
    handler = AsyncMock()
    with (
        patch(
            "manager_server.infrastructure.config.settings.agent_runtime_endpoint",
            "http://runtime:8091",
        ),
        patch(
            "manager_server.core.instance_resource.runtime_config_sync.sync_runtime_config",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as sync_mock,
    ):
        await maybe_full_sync_runtime_on_online(
            handler, "jid-1", previous_runtime_status="offline"
        )
    sync_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_skip_when_endpoint_empty():
    handler = AsyncMock()
    with (
        patch(
            "manager_server.infrastructure.config.settings.agent_runtime_endpoint",
            "  ",
        ),
        patch(
            "manager_server.core.instance_resource.runtime_config_sync.sync_runtime_config",
            new_callable=AsyncMock,
        ) as sync_mock,
    ):
        await maybe_full_sync_runtime_on_online(
            handler, "jid-1", previous_runtime_status="offline"
        )
    sync_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_does_not_raise():
    handler = AsyncMock()
    with (
        patch(
            "manager_server.infrastructure.config.settings.agent_runtime_endpoint",
            "http://runtime:8091",
        ),
        patch(
            "manager_server.core.instance_resource.runtime_config_sync.sync_runtime_config",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
    ):
        await maybe_full_sync_runtime_on_online(
            handler, "jid-1", previous_runtime_status="offline"
        )


@pytest.mark.asyncio
async def test_apply_health_probe_triggers_runtime_full_sync():
    from manager_server.core.instance.instance_service import apply_health_probe_result

    handler = AsyncMock()
    row = MagicMock(runtime_status="offline")
    with (
        patch(
            "manager_server.core.instance.instance_service.get_instance_row",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "manager_server.core.instance.instance_service.maybe_full_sync_runtime_on_online",
            new_callable=AsyncMock,
        ) as sync_mock,
    ):
        ok = await apply_health_probe_result(
            handler,
            jiuwenclaw_id="jid-1",
            service_type="runtime",
            alive=True,
        )
    assert ok is True
    sync_mock.assert_awaited_once_with(
        handler, "jid-1", previous_runtime_status="offline"
    )
