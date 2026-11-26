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
async def test_probe_rejects_non_http_url():
    with pytest.raises(ValueError, match="http"):
        await probe_config_host("gw.example:8080", side="gateway")


@pytest.mark.asyncio
@pytest.mark.parametrize(("base", "expected"), [
    ("http://jiuwenswarm-web.alpha:5173", "https://jiuwenswarm-web.alpha:5173/"),
    ("http://web-custom:5173", "https://web-custom:5173/"),
    ("https://web.example.org", "https://web.example.org/"),
    ("https://web-custom.beta.example.org", "https://web-custom.beta.example.org/"),
    ("http://localhost:5173", "http://localhost:5173/"),
])
async def test_user_web_probe_request_uses_expected_url(base, expected, tmp_path, monkeypatch):
    target = "http://localhost:5173" if base.startswith("http://localhost:") else "https://web-custom.alpha.svc:5173"
    monkeypatch.setenv("NAMESPACE", "alpha")
    monkeypatch.setenv("MANAGER_WEB_USER_WEB_TARGET", target)
    monkeypatch.setattr("manager_server.core.instance.config_host_probe._WEB_PROBE_CA", tmp_path)
    # A CA in another namespace must not affect any of these requests.
    (tmp_path / "web-custom.beta.crt").write_text("invalid CA")
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = MagicMock(status_code=200)
    with patch("manager_server.core.instance.config_host_probe.httpx.AsyncClient", return_value=client) as factory:
        await probe_config_host(base, side="user_web")
    assert factory.call_args.kwargs["verify"] is True
    client.get.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_user_web_probe_rejects_invalid_ca_for_selected_service(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGER_WEB_USER_WEB_TARGET", "https://web-custom.alpha.svc.cluster.local:5173")
    monkeypatch.setattr("manager_server.core.instance.config_host_probe._WEB_PROBE_CA", tmp_path)
    (tmp_path / "web-custom.beta.crt").write_text("invalid CA")
    with pytest.raises(ValueError, match="user_web probe trust configuration failed"):
        await probe_config_host("http://web-custom.beta.svc.cluster.local:5173", side="user_web")
