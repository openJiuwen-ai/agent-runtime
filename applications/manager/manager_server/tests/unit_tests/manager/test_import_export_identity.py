from __future__ import annotations

import hashlib
from types import SimpleNamespace

import httpx
import pytest
from conftest import ManagerApiHarness

from manager_server.core.import_export import cluster
from manager_server.core.import_export.cluster import _identity_base_url, _identity_request
from manager_server.core.import_export.registry import (
    IdentityServiceUnavailableError,
    SheetData,
    WorkbookData,
)
from manager_server.core.import_export.workbook import dump_workbook
from manager_server.routers import import_export_routers

_IDENTITY_BASE = "http://identity.example:8770"
_FALLBACK_BASE = "http://legacy.example:8770"
_ORG_PATH = "/v1/orgs/org-1"


@pytest.mark.parametrize(
    ("public_key_url", "fallback_url", "expected"),
    [
        (f"{_IDENTITY_BASE}/v1/auth/public_key", _FALLBACK_BASE, _IDENTITY_BASE),
        (f" {_IDENTITY_BASE}/v1/auth/public_key/ ", _FALLBACK_BASE, _IDENTITY_BASE),
        (
            f"{_IDENTITY_BASE}/idp/v1/auth/public_key",
            _FALLBACK_BASE,
            f"{_IDENTITY_BASE}/idp",
        ),
        (f"{_IDENTITY_BASE}/custom-key", f" {_FALLBACK_BASE}/ ", _FALLBACK_BASE),
        ("", _FALLBACK_BASE, _FALLBACK_BASE),
    ],
)
def test_identity_base_url(public_key_url: str, fallback_url: str, expected: str):
    assert _identity_base_url(public_key_url, fallback_url) == expected


def _install_identity_transport(monkeypatch: pytest.MonkeyPatch, transport_handler) -> None:
    client_type = httpx.AsyncClient

    def create_client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"] == 10.0
        return client_type(transport=httpx.MockTransport(transport_handler), **kwargs)

    monkeypatch.setattr(cluster.httpx, "AsyncClient", create_client)
    monkeypatch.setattr(
        cluster,
        "settings",
        SimpleNamespace(
            identity_public_key_url=f"{_IDENTITY_BASE}/v1/auth/public_key",
            manager_web_idp_target=_FALLBACK_BASE,
        ),
    )


@pytest.mark.asyncio
async def test_identity_request_uses_public_key_base_and_forwards_authorization(
    monkeypatch: pytest.MonkeyPatch,
):
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{_IDENTITY_BASE}{_ORG_PATH}"
        assert request.headers["Authorization"] == "Bearer test"
        assert request.method == "POST"
        assert request.content == b'{"group_id":"org-1"}'
        return httpx.Response(200, json={"group_id": "org-1"})

    _install_identity_transport(monkeypatch, respond)
    result = await _identity_request("POST", _ORG_PATH, "Bearer test", body={"group_id": "org-1"})
    assert result == {"group_id": "org-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout]
)
async def test_identity_request_reports_transport_failure(
    monkeypatch: pytest.MonkeyPatch, error_type
):
    def fail(request: httpx.Request) -> httpx.Response:
        raise error_type("connection failed", request=request)

    _install_identity_transport(monkeypatch, fail)
    with pytest.raises(IdentityServiceUnavailableError, match="identity center is unavailable"):
        await _identity_request("GET", _ORG_PATH, "Bearer test")


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 404, 403])
async def test_identity_request_preserves_response_handling(
    monkeypatch: pytest.MonkeyPatch, status_code: int
):
    _install_identity_transport(
        monkeypatch, lambda _: httpx.Response(status_code, json={"group_id": "org-1"})
    )
    if status_code == 403:
        with pytest.raises(ValueError, match="identity center request failed"):
            await _identity_request("GET", _ORG_PATH, None)
    else:
        result = await _identity_request("GET", _ORG_PATH, None)
        assert result == ({"group_id": "org-1"} if status_code == 200 else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["export-one", "export-many", "preflight", "import"])
async def test_import_export_routes_report_identity_unavailable(
    manager_api: ManagerApiHarness, monkeypatch: pytest.MonkeyPatch, operation: str
):
    async def unavailable(*_args):
        raise IdentityServiceUnavailableError("identity center is unavailable")

    adapter = SimpleNamespace(
        export=unavailable, export_many=unavailable, preflight=unavailable, apply=unavailable
    )
    monkeypatch.setattr(import_export_routers, "_adapter", lambda _: adapter)
    base = "/api/v1/import-export/cluster"
    payload = dump_workbook(
        WorkbookData(
            resource_type="cluster",
            resource_id="cluster-1",
            resource_name="Cluster",
            sheets=[SheetData("01_Cluster", ["jiuwenclaw_id"], [{"jiuwenclaw_id": "cluster-1"}])],
        )
    )
    if operation == "export-one":
        response = await manager_api.http.get(f"{base}/cluster-1/export")
    elif operation == "export-many":
        response = await manager_api.http.post(
            f"{base}/export", json={"resource_ids": ["cluster-1"]}
        )
    else:
        params = (
            {"confirmation_token": hashlib.sha256(payload).hexdigest()}
            if operation == "import"
            else {}
        )
        response = await manager_api.http.post(
            f"{base}/{operation}", content=payload, params=params
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "identity center is unavailable"}
