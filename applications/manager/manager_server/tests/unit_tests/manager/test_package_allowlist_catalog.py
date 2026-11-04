# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""向 Hub 要专家和专家团清单，供专家包模板勾选。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from manager_server.core.application_config.hub_package_catalog import (
    fetch_platform_package_catalog,
)

pytestmark = pytest.mark.unit

_HIDDEN = "794db1bfe8884d6897d62d3584894a59"


class _HubResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self) -> dict:
        return self._payload


class _HubClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self) -> "_HubClient":
        return self

    async def __aexit__(self, *args) -> bool:
        del args
        return False

    async def get(self, url: str, params: dict | None = None) -> _HubResponse:
        calls.append((url, dict(params or {})))
        plugin_type = (params or {}).get("plugin_type")
        page = int((params or {}).get("page") or 1)
        if plugin_type == "agent-template" and page == 1:
            return _HubResponse({
                "code": 200,
                "data": {
                    "items": [
                        {
                            "asset_id": "asset-1",
                            "plugin_type": "agent-template",
                            "display_name": "专家甲",
                            "public_latest_version": "1.2.0",
                            "short_desc": "销售",
                            "download_url": "https://example.invalid/pkg",
                        },
                        {
                            "asset_id": _HIDDEN,
                            "plugin_type": "agent-template",
                            "display_name": "隐藏",
                            "public_latest_version": "1.0.0",
                        },
                        {
                            "asset_id": "not-this-kind",
                            "plugin_type": "agent-group",
                            "display_name": "种类不符",
                        },
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 100,
                },
            })
        return _HubResponse({
            "code": 200,
            "data": {"items": [], "total": 0, "page": page, "page_size": 100},
        })


calls: list[tuple[str, dict]] = []


@pytest.mark.asyncio
async def test_catalog_reads_hub_platform_not_a_cluster() -> None:
    calls.clear()
    with (
        patch(
            "manager_server.core.application_config.hub_package_catalog.settings",
            SimpleNamespace(
                team_skills_hub_base_url="https://hub.example",
                team_skills_hub_timeout=30,
            ),
        ),
        patch(
            "manager_server.core.application_config.hub_package_catalog.httpx.AsyncClient",
            _HubClient,
        ),
    ):
        payload = await fetch_platform_package_catalog()

    assert calls == [
        ("https://hub.example/api/v1/plugins", {
            "plugin_type": "agent-template",
            "page": 1,
            "page_size": 100,
        }),
        ("https://hub.example/api/v1/plugins", {
            "plugin_type": "agent-group",
            "page": 1,
            "page_size": 100,
        }),
    ]
    assert payload["error"] == ""
    assert payload["items"] == [
        {
            "kind": "agent_template",
            "id": "asset-1",
            "display_name": "专家甲",
            "version": "1.2.0",
            "description": "销售",
        }
    ]
    assert "download_url" not in payload["items"][0]


@pytest.mark.asyncio
async def test_catalog_failure_returns_empty_items_and_error() -> None:
    class _Broken(_HubClient):
        async def get(self, url: str, params: dict | None = None) -> _HubResponse:
            del url, params
            raise RuntimeError("hub down")

    with patch(
        "manager_server.core.application_config.hub_package_catalog.httpx.AsyncClient",
        _Broken,
    ):
        payload = await fetch_platform_package_catalog()

    assert payload["items"] == []
    assert payload["error"] == "catalog unavailable"
