# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Local offline mode keeps Manager CRUD real and cluster connectivity unverified."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from conftest import ManagerApiHarness
from demo_payloads import instance_create_body
from manager_server.core.instance import config_host_probe, instance_service, runtime_identity
from manager_server.infrastructure.config import Settings, settings
from manager_server.infrastructure.dev_mode import offline_dev_enabled
from manager_server.manager_config_push import client as push_client
from manager_server.manager_config_push.client import gateway_request, runtime_request
from manager_server.models.instance_models import INSTANCE_INFO_TABLE_DEF
from manager_server.schedulers import heartbeat_scanner

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_dev_settings(monkeypatch: pytest.MonkeyPatch):
    """Do not let a developer's launcher environment control test behavior."""
    monkeypatch.setattr(settings, "dev_offline", False)
    monkeypatch.setattr(settings, "local_dev", False)
    monkeypatch.setattr(settings, "rest_host", "0.0.0.0")
    monkeypatch.setattr(settings, "db_type", "sqlite")


@pytest.fixture
def offline_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "dev_offline", True)
    monkeypatch.setattr(settings, "local_dev", True)
    monkeypatch.setattr(settings, "rest_host", "127.0.0.1")
    return settings


def test_offline_is_opt_in():
    assert Settings.model_fields["dev_offline"].default is False
    assert Settings.model_fields["local_dev"].default is False
    assert offline_dev_enabled() is False


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_offline_accepts_loopback_sqlite_launcher(offline_mode, monkeypatch, host):
    monkeypatch.setattr(settings, "rest_host", host)
    assert offline_dev_enabled() is True


@pytest.mark.parametrize(
    ("field", "value"),
    [("local_dev", False), ("rest_host", "0.0.0.0"), ("db_type", "postgres")],
)
def test_offline_rejects_missing_local_guards(offline_mode, monkeypatch, field, value):
    monkeypatch.setattr(settings, field, value)
    with pytest.raises(RuntimeError, match="local development launcher"):
        offline_dev_enabled()


@pytest.mark.asyncio
async def test_offline_validates_urls_without_opening_network(offline_mode, monkeypatch):
    network = MagicMock(side_effect=AssertionError("offline mode opened HTTP transport"))
    monkeypatch.setattr(config_host_probe.httpx, "AsyncClient", network)
    await config_host_probe.require_config_hosts_reachable(
        gateway_host="http://gateway.default:19000",
        runtime_host="https://runtime.default:8091",
        user_web_host="http://web.default:5173",
    )
    assert await config_host_probe.check_config_host_alive(
        "http://gateway.default:19000", side="gateway"
    ) is False
    network.assert_not_called()


@pytest.mark.parametrize("host", ["gateway.default:19000", "file:///tmp/config", "http://"])
@pytest.mark.parametrize("field", ["gateway_host", "runtime_host", "user_web_host"])
@pytest.mark.asyncio
async def test_offline_still_rejects_invalid_urls(offline_mode, monkeypatch, field, host):
    network = MagicMock(side_effect=AssertionError("offline mode opened HTTP transport"))
    monkeypatch.setattr(config_host_probe.httpx, "AsyncClient", network)
    with pytest.raises(ValueError, match=r"http\(s\) URL"):
        await config_host_probe.require_config_hosts_reachable(**{field: host})
    network.assert_not_called()


@pytest.mark.asyncio
async def test_strict_mode_preserves_reachability_failure(monkeypatch):
    probe = AsyncMock(side_effect=ValueError("gateway health check failed"))
    monkeypatch.setattr(config_host_probe, "probe_config_host", probe)
    with pytest.raises(ValueError, match="gateway health check failed"):
        await config_host_probe.require_config_hosts_reachable(
            gateway_host="http://gateway.default:19000"
        )
    probe.assert_awaited_once_with(
        "http://gateway.default:19000", side="gateway", timeout=5.0
    )


@pytest.mark.parametrize("role", ["gateway", "runtime"])
@pytest.mark.asyncio
async def test_offline_push_resolves_endpoint_without_http(offline_mode, monkeypatch, role):
    lookup = AsyncMock(return_value=f"http://{role}.default:19000")
    transport = AsyncMock(side_effect=AssertionError("offline push used HTTP"))
    monkeypatch.setattr(push_client, f"require_{role}_endpoint", lookup)
    monkeypatch.setattr(push_client, "http_request", transport)
    request = gateway_request if role == "gateway" else runtime_request
    result = await request("cluster-1", "POST", "/api/config", {"example": True})
    lookup.assert_awaited_once_with("cluster-1")
    transport.assert_not_awaited()
    assert result["simulated"] is True
    assert result["transport"] == "local-offline"
    if role == "gateway":
        assert result["success_flag"] is True
        assert result["result"]["items"] == []
    else:
        assert result["ok"] is True


@pytest.mark.parametrize("role", ["gateway", "runtime"])
@pytest.mark.asyncio
async def test_offline_push_still_rejects_unknown_instance(offline_mode, monkeypatch, role):
    lookup = AsyncMock(side_effect=ValueError("instance not found"))
    transport = AsyncMock()
    monkeypatch.setattr(push_client, f"require_{role}_endpoint", lookup)
    monkeypatch.setattr(push_client, "http_request", transport)
    request = gateway_request if role == "gateway" else runtime_request
    with pytest.raises(ValueError, match="instance not found"):
        await request("missing", "POST", "/api/config", {})
    lookup.assert_awaited_once_with("missing")
    transport.assert_not_awaited()


@pytest.mark.parametrize("role", ["gateway", "runtime"])
@pytest.mark.asyncio
async def test_disabling_offline_restores_real_push_failure(monkeypatch, role):
    lookup = AsyncMock(return_value=f"http://{role}.default:19000")
    transport = AsyncMock(side_effect=ValueError("HTTP push failed"))
    monkeypatch.setattr(push_client, f"require_{role}_endpoint", lookup)
    monkeypatch.setattr(push_client, "http_request", transport)
    request = gateway_request if role == "gateway" else runtime_request
    with pytest.raises(ValueError, match="HTTP push failed"):
        await request("cluster-1", "POST", "/api/config", {})
    transport.assert_awaited_once()


@pytest.mark.asyncio
async def test_offline_scanner_skips_without_claiming_online(offline_mode, monkeypatch):
    rows = [
        SimpleNamespace(
            jiuwenclaw_id="cluster-1", gateway_host="http://gw",
            runtime_host="http://rt", user_web_host="http://web",
        ),
        SimpleNamespace(
            jiuwenclaw_id="cluster-2", gateway_host="http://gw2",
            runtime_host=None, user_web_host=None,
        ),
    ]
    handler = SimpleNamespace(list_records=AsyncMock(return_value=rows), update=AsyncMock())
    probe = AsyncMock(side_effect=AssertionError("offline scanner probed a host"))
    health_update = AsyncMock(side_effect=AssertionError("offline scanner changed health"))
    monkeypatch.setattr(heartbeat_scanner, "check_config_host_alive", probe)
    monkeypatch.setattr(heartbeat_scanner, "apply_health_probe_result", health_update)
    assert await heartbeat_scanner.scan_instance_health_once(handler) == {
        "probed": 0, "alive": 0, "dead": 0, "skipped": 4,
    }
    probe.assert_not_awaited()
    health_update.assert_not_awaited()
    handler.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_offline_instance_create_update_persist_with_pending_health(
    manager_api: ManagerApiHarness, offline_mode, monkeypatch,
):
    # The shared harness replaces these functions. Restore the real validation
    # path and fail if creation attempts its second Runtime probe or full sync.
    monkeypatch.setattr(
        instance_service, "require_config_hosts_reachable",
        config_host_probe.require_config_hosts_reachable,
    )
    runtime_probe = AsyncMock(side_effect=AssertionError("Runtime health was requested"))
    health_update = AsyncMock(side_effect=AssertionError("offline create marked online"))
    network = MagicMock(side_effect=AssertionError("offline CRUD opened HTTP transport"))
    monkeypatch.setattr(runtime_identity, "fetch_runtime_identity_from_health", runtime_probe)
    monkeypatch.setattr(instance_service, "apply_health_probe_result", health_update)
    monkeypatch.setattr(config_host_probe.httpx, "AsyncClient", network)
    body = instance_create_body(
        jiuwenclaw_name="Offline integration cluster",
        gateway_host="http://gw.local-dev.invalid:19000",
        runtime_host="http://rt.local-dev.invalid:8091",
        user_web_host="http://web.local-dev.invalid:5173",
        gateway_web_http_host="http://gw.local-dev.invalid:19002",
        gateway_web_ws_host="http://gw.local-dev.invalid:19000",
    )
    created = await manager_api.http.post(manager_api.instances_url(), json=body)
    assert created.status_code == 200, created.text
    jid = created.json()["data"]["jiuwenclaw_id"]
    for side in ("gateway", "runtime", "user_web"):
        assert created.json()["data"][f"{side}_status"] == "pending"

    updated_host = "http://gw-updated.local-dev.invalid:19000"
    updated = await manager_api.http.patch(
        manager_api.instances_url(f"/{jid}"),
        json={"gateway_host": updated_host, "description": "saved offline"},
    )
    assert updated.status_code == 200, updated.text
    stored = await manager_api.handler.get(
        INSTANCE_INFO_TABLE_DEF.table_name, {"jiuwenclaw_id": jid}
    )
    assert stored is not None
    assert stored.gateway_host == updated_host
    assert stored.description == "saved offline"
    for side in ("gateway", "runtime", "user_web"):
        assert getattr(stored, f"{side}_status") == "pending"
        assert getattr(stored, f"{side}_last_alive") is None
    runtime_probe.assert_not_awaited()
    health_update.assert_not_awaited()
    network.assert_not_called()
    assert manager_api.gateway_sim.calls == []
