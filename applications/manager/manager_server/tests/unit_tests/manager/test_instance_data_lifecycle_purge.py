# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例删除时 Manager / Runtime / Gateway 清理覆盖。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from manager_server.core.instance.instance_data_lifecycle import (
    purge_instance_all_data,
    purge_manager_instance_data,
    purge_runtime_instance_data,
)
from manager_server.models.instance_access_models import INSTANCE_GRANT_TABLE_DEF
from manager_server.models.instance_resource_models import (
    INSTANCE_AGENT_RESOURCE_TABLE_DEF,
    INSTANCE_SERVICE_RESOURCE_TABLE_DEF,
)

pytestmark = pytest.mark.unit

_AGENT = INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name
_SERVICE = INSTANCE_SERVICE_RESOURCE_TABLE_DEF.table_name
_GRANT = INSTANCE_GRANT_TABLE_DEF.table_name


@pytest.mark.asyncio
async def test_purge_manager_deletes_instance_resources_and_grants():
    handler = AsyncMock()

    async def list_records(table, filters, **_kwargs):
        if table == _SERVICE:
            return [MagicMock(id=11)]
        if table == _AGENT:
            return [MagicMock(id=21)]
        if table == _GRANT:
            return [MagicMock(id=31)]
        return []

    handler.list_records = AsyncMock(side_effect=list_records)
    handler.delete = AsyncMock(return_value=True)

    counts = await purge_manager_instance_data(handler, "jid-1")

    assert counts.get(_SERVICE) == 1
    assert counts.get(_AGENT) == 1
    assert counts.get(_GRANT) == 1
    deleted_tables = {call.args[0] for call in handler.delete.await_args_list}
    assert _SERVICE in deleted_tables
    assert _AGENT in deleted_tables
    assert _GRANT in deleted_tables


@pytest.mark.asyncio
async def test_purge_runtime_pushes_empty_projection():
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
        result = await purge_runtime_instance_data(handler, "jid-1")

    assert result == {"purged": True}
    sync_mock.assert_awaited_once_with(handler, "jid-1", resource_rows=[])


@pytest.mark.asyncio
async def test_purge_runtime_skips_when_endpoint_empty():
    handler = AsyncMock()
    with (
        patch(
            "manager_server.infrastructure.config.settings.agent_runtime_endpoint",
            "",
        ),
        patch(
            "manager_server.core.instance_resource.runtime_config_sync.sync_runtime_config",
            new_callable=AsyncMock,
        ) as sync_mock,
    ):
        result = await purge_runtime_instance_data(handler, "jid-1")

    assert result.get("skipped") is True
    sync_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_instance_all_data_order_runtime_then_gateway_then_manager():
    handler = AsyncMock()
    order: list[str] = []

    async def runtime(_handler, jid):
        order.append("runtime")
        return {"purged": True}

    async def gateway(jid):
        order.append("gateway")
        return {"purged": True}

    async def manager(_handler, jid):
        order.append("manager")
        return {"instance_service_resource": 1}

    with (
        patch(
            "manager_server.core.instance.instance_data_lifecycle.purge_runtime_instance_data",
            new_callable=AsyncMock,
            side_effect=runtime,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.purge_gateway_instance_data",
            new_callable=AsyncMock,
            side_effect=gateway,
        ),
        patch(
            "manager_server.core.instance.instance_data_lifecycle.purge_manager_instance_data",
            new_callable=AsyncMock,
            side_effect=manager,
        ),
    ):
        result = await purge_instance_all_data(handler, "jid-1")

    assert order == ["runtime", "gateway", "manager"]
    assert result["runtime"]["purged"] is True
    assert result["gateway"]["purged"] is True
    assert result["manager"]["instance_service_resource"] == 1
