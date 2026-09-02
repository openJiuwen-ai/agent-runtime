# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""全量同步应下发 logging / memory / task-memory（permissions 走模板，不单独 push）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_sync_data_pushes_application_configs():
    from manager_server.core.instance.instance_data_lifecycle import (
        sync_data_to_gateway_on_register,
    )

    handler = AsyncMock()
    ack = {"success_flag": True, "result": {"synced": True}, "transport": "http"}

    with (
        patch(
            "manager_server.core.instance.instance_data_lifecycle.sync_referenced_templates_to_gateway",
            new_callable=AsyncMock,
            return_value={"model": ack},
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_agent_resources_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_logging_config_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ) as logging_mock,
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_task_memory_config_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ) as task_memory_mock,
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_memory_config_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ) as memory_mock,
        patch(
            "manager_server.core.instance.instance_data_lifecycle.push_log_masking_rules_sync_to_gateway",
            new_callable=AsyncMock,
            return_value=ack,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle._seed_log_masking_if_needed",
            new_callable=AsyncMock,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.rebuild_jid_template_ref_for_gateway",
            new_callable=AsyncMock,
        ),
    ):
        results = await sync_data_to_gateway_on_register(handler, "jid-app")

    logging_mock.assert_awaited_once_with(handler, "jid-app")
    task_memory_mock.assert_awaited_once_with(handler, "jid-app")
    memory_mock.assert_awaited_once_with(handler, "jid-app")
    assert "logging" in results
    assert "task_memory" in results
    assert "memory" in results
    assert "permissions" not in results


@pytest.mark.asyncio
async def test_push_logging_config_sync_puts_manager_row():
    from manager_server.core.application_config.logging_config import (
        push_logging_config_sync_to_gateway,
    )

    row = MagicMock(
        level="WARNING",
        console_level="ERROR",
        gateway="INFO",
        channel=None,
        agent_server="DEBUG",
        full=None,
    )
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=row)

    with patch(
        "manager_server.core.application_config.logging_config.gateway_request",
        new_callable=AsyncMock,
        return_value={"success_flag": True, "result": {}, "transport": "http"},
    ) as gw_mock:
        ack = await push_logging_config_sync_to_gateway(handler, "jid-1")

    assert ack["success_flag"] is True
    gw_mock.assert_awaited_once_with(
        "jid-1",
        "PUT",
        "/api/v1/logging",
        {
            "level": "WARNING",
            "console_level": "ERROR",
            "gateway": "INFO",
            "channel": None,
            "agent_server": "DEBUG",
            "full": None,
        },
    )


@pytest.mark.asyncio
async def test_push_logging_config_sync_skips_when_missing():
    from manager_server.core.application_config.logging_config import (
        push_logging_config_sync_to_gateway,
    )

    handler = AsyncMock()
    handler.get = AsyncMock(return_value=None)

    with patch(
        "manager_server.core.application_config.logging_config.gateway_request",
        new_callable=AsyncMock,
    ) as gw_mock:
        ack = await push_logging_config_sync_to_gateway(handler, "jid-1")

    gw_mock.assert_not_awaited()
    assert ack["result"]["synced"] is False
