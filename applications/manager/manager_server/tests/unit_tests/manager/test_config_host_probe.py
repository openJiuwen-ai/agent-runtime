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
            gateway_host=None,
            runtime_host="http://rt.example:8090",
        )
    probe.assert_awaited_once_with(
        "http://rt.example:8090", side="runtime", timeout=5.0
    )


@pytest.mark.asyncio
async def test_probe_user_web_ok():
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html></html>"
    resp.json.side_effect = ValueError("not json")

    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "manager_server.core.instance.config_host_probe.httpx.AsyncClient",
        return_value=client,
    ):
        await probe_config_host("http://web.example:5173", side="user_web")

    client.get.assert_awaited_once_with("http://web.example:5173/")


@pytest.mark.asyncio
async def test_require_probes_user_web_when_set():
    with patch(
        "manager_server.core.instance.config_host_probe.probe_config_host",
        new_callable=AsyncMock,
    ) as probe:
        await require_config_hosts_reachable(
            user_web_host="http://web.example:5173",
        )
    probe.assert_awaited_once_with(
        "http://web.example:5173", side="user_web", timeout=5.0
    )


@pytest.mark.asyncio
async def test_probe_rejects_non_http_url():
    with pytest.raises(ValueError, match="http"):
        await probe_config_host("gw.example:8080", side="gateway")


def test_managed_web_probe_upgrade_and_scoped_trust(tmp_path, monkeypatch):
    from manager_server.core.instance import config_host_probe as probe

    monkeypatch.setattr(probe, "_WEB_PROBE_CA", tmp_path)
    monkeypatch.setenv("NAMESPACE", "alpha")
    monkeypatch.setenv("MANAGER_WEB_USER_WEB_TARGET", "https://web-custom.alpha.svc.cluster.local:5173")
    cert = tmp_path / "web-custom.beta.crt"
    cert.write_text("intentionally invalid CA")
    assert probe._user_web_probe("http://jiuwenswarm-web.alpha:5173") == (
        "https://jiuwenswarm-web.alpha:5173", True,
    )
    assert probe._user_web_probe("http://web-custom:5173") == ("https://web-custom:5173", True)
    assert probe._user_web_probe("https://web.example.org") == ("https://web.example.org", True)
    assert probe._user_web_probe("https://web-custom.beta.example.org") == (
        "https://web-custom.beta.example.org", True,
    )
    with pytest.raises(OSError):
        probe._user_web_probe("http://web-custom.beta.svc.cluster.local:5173")
    monkeypatch.setenv("MANAGER_WEB_USER_WEB_TARGET", "http://localhost:5173")
    assert probe._user_web_probe("http://localhost:5173") == ("http://localhost:5173", True)
