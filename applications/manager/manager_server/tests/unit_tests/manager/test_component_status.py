"""component-status aggregation unit tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from manager_server.core.instance.component_status import build_component_status
from manager_server.infrastructure.k8s_inventory import (
    ComponentInventory,
    NamespaceInventory,
    PodInventoryItem,
    format_cpu_millicores,
    format_memory_mib,
)

pytestmark = pytest.mark.unit


def test_format_cpu_and_memory():
    assert format_cpu_millicores("500m") == "500m"
    assert format_cpu_millicores("1000000000n") == "1"
    assert format_cpu_millicores("2") == "2"
    assert format_memory_mib("1048576") == "1.0Mi"
    assert format_memory_mib("128Mi") == "128Mi"


@pytest.mark.asyncio
async def test_build_component_status_aggregates_agentserver_from_pods():
    handler = AsyncMock()
    row = SimpleNamespace(
        jiuwenclaw_id="jc-1",
        namespace="wx",
        gateway_host="http://gw:19000",
        runtime_host="http://rt:8091",
        gateway_status="online",
        gateway_last_alive=None,
        runtime_status="online",
        runtime_last_alive=None,
    )
    inventory = NamespaceInventory(
        namespace="wx",
        components=[
            ComponentInventory(
                type="gateway",
                pods=[
                    PodInventoryItem(
                        name="jiuwenswarm-gateway-a",
                        phase="Running",
                        ready=True,
                        restarts=0,
                        cpu="120m",
                        memory="256Mi",
                    )
                ],
            ),
            ComponentInventory(
                type="runtime",
                pods=[
                    PodInventoryItem(
                        name="jiuwenswarm-agent-runtime-a",
                        phase="Running",
                        ready=True,
                        restarts=1,
                        cpu="80m",
                        memory="512Mi",
                    )
                ],
            ),
            ComponentInventory(
                type="agentserver",
                pods=[
                    PodInventoryItem(
                        name="jiuwenswarm-agentserver-0",
                        phase="Running",
                        ready=True,
                        restarts=0,
                        cpu="200m",
                        memory="1.0Gi",
                    ),
                    PodInventoryItem(
                        name="jiuwenswarm-agentserver-1",
                        phase="Running",
                        ready=False,
                        restarts=2,
                        cpu="10m",
                        memory="64Mi",
                    ),
                ],
            ),
        ],
    )

    with (
        patch(
            "manager_server.core.instance.component_status.get_instance_row",
            AsyncMock(return_value=row),
        ),
        patch(
            "manager_server.core.instance.component_status.list_component_inventory",
            AsyncMock(return_value=inventory),
        ),
        patch(
            "manager_server.core.instance.component_status.runtime_probe_enabled",
            return_value=True,
        ),
    ):
        data = await build_component_status(handler, jiuwenclaw_id="jc-1")

    assert data is not None
    assert data["namespace"] == "wx"
    by_type = {item["type"]: item for item in data["components"]}
    assert by_type["gateway"]["status"] == "online"
    assert by_type["runtime"]["status"] == "online"
    assert by_type["agentserver"]["status"] == "pending"
    assert len(by_type["agentserver"]["pods"]) == 2
    assert by_type["gateway"]["pods"][0]["cpu"] == "120m"


@pytest.mark.asyncio
async def test_build_component_status_missing_instance():
    with patch(
        "manager_server.core.instance.component_status.get_instance_row",
        AsyncMock(return_value=None),
    ):
        assert await build_component_status(AsyncMock(), jiuwenclaw_id="missing") is None
