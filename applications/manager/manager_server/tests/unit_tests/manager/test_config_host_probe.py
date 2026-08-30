# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""config_host_probe 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from manager_server.core.instance.config_host_probe import (
    probe_config_host,
    require_config_hosts_reachable,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_probe_gateway_ok():
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "ok"
    resp.json.return_value = {"code": 200}

    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "manager_server.core.instance.config_host_probe.httpx.AsyncClient",
        return_value=client,
    ):
        await probe_config_host("http://gw.example:8080", side="gateway")

    client.get.assert_awaited_once_with("http://gw.example:8080/api/health")


@pytest.mark.asyncio
async def test_probe_runtime_rejects_ok_false():
    resp = MagicMock()
    resp.status_code = 200
    resp.text = '{"ok":false}'
    resp.json.return_value = {"ok": False}

    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "manager_server.core.instance.config_host_probe.httpx.AsyncClient",
        return_value=client,
    ):
        with pytest.raises(ValueError, match="not ready"):
            await probe_config_host("http://rt.example:8090", side="runtime")


@pytest.mark.asyncio
async def test_require_probes_runtime_when_set():
    with patch(
        "manager_server.core.instance.config_host_probe.probe_config_host",
        new_callable=AsyncMock,
    ) as probe:
        await require_config_hosts_reachable(
            gateway_config_host=None,
            runtime_config_host="http://rt.example:8090",
        )
    probe.assert_awaited_once_with(
        "http://rt.example:8090", side="runtime", timeout=5.0
    )


@pytest.mark.asyncio
async def test_probe_rejects_non_http_url():
    with pytest.raises(ValueError, match="http"):
        await probe_config_host("gw.example:8080", side="gateway")
